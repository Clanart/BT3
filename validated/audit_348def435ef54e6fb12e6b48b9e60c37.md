## Finding: Live decimals re-read in `send` breaks the fixed ERC20 conversion ratio, enabling over-minting on the destination chain

### Summary
`Pallet::send` escrows or burns `params.amount` of the local asset (in raw storage units, unaffected by metadata), then computes the outbound `erc20_amount` using the **live** result of `Assets::decimals()` combined with `Precisions` (`erc_decimals`), which was fixed **once** at `register_token`/`update_token` time. Because the local asset's `decimals` metadata can be changed by the asset's owner after registration (an unprivileged, non-bridge-governance account) via `pallet_assets::set_metadata`, an attacker who owns the registered asset's metadata can shrink `local_decimals` between registration and a `send` call, inflating the multiplier `10^(erc_decimals - local_decimals)` used by `convert_to_erc20`, and thus mint an ERC20 amount on the destination chain that is disproportionate to the raw units actually escrowed/burned.

### Finding Description
In `send`: [1](#0-0) 

`erc_decimals` is read from `Precisions`, set once at `register_token`/`update_token` time: [2](#0-1) 

but `decimals` used in the same call is fetched **live** via `fungibles::metadata::Inspect::decimals`, not the value captured/validated at registration: [3](#0-2) 

The conversion itself amplifies by `10^(erc_decimals - local_decimals)`: [4](#0-3) 

`register_token`/`update_token` only assert `config.decimals >= local_decimals` **at the moment of registration**; nothing pins or re-validates this relationship afterward: [5](#0-4) 

The underlying `Assets::burn_from`/`Assets::transfer` operate on raw balance units and are completely decimals-agnostic — decimals is pure display/scaling metadata that `pallet_assets` lets the asset's `owner` mutate via `set_metadata`, independent of the `CreateOrigin`/governance that performed `register_token`. Confirmed in the runtime wiring, asset creation's `CreateOrigin` (e.g. `EnsureRootWithSuccess`) only gates *creation*, while the resulting asset's `owner`/`admin` account (which may be any account, including a non-governance/attacker-controlled address for community/partner-issued tokens) retains permission to call `set_metadata` and change `decimals` at will: [6](#0-5) 

So the exploit path is:
1. A token whose local asset metadata is owned by an unprivileged (non-bridge-governance) account is registered via `register_token`, fixing `Precisions[asset][dest] = erc_decimals` based on `local_decimals` observed at that time (e.g. `local_decimals = 10`, `erc_decimals = 18`).
2. The asset owner calls `pallet_assets::set_metadata` to reduce `decimals` (e.g. to `0`).
3. The owner (or any holder) calls `send` with some `amount` of raw asset units. The burn/escrow removes exactly `amount` raw units, but `decimals` is now re-read as `0`, so `convert_to_erc20` multiplies by `10^18` instead of the originally-intended `10^8` — an unintended 10-order-of-magnitude inflation of the value encoded in the outbound `Message.amount`.
4. On the destination chain, the paired `HyperFungibleToken`/`WrappedHyperFungibleToken` contract mints/releases `erc20_amount` tokens to the recipient based on that inflated value, which is disconnected from the actual amount removed from local custody/supply.

### Impact Explanation
This breaks the core bridging invariant that the amount minted/released on the destination chain must equal the value actually removed from source-chain custody, scaled by the fixed decimal ratio established at registration. An attacker controlling (or colluding with) the metadata-owner of a registered asset can burn/escrow a small raw amount locally and cause an arbitrarily larger ERC20 amount to be minted/released on the destination EVM chain, over-minting the wrapped asset and eventually draining the backing reserve or over-issuing debt-free wrapped supply once redeemed. This falls squarely in the "stealing or loss of funds / wrongful amount" impact category from the bounty scope.

### Likelihood Explanation
Requires the targeted asset's metadata-owner (an unprivileged account distinct from the `CreateOrigin`/bridge governance) to call `pallet_assets::set_metadata` after the token has been registered with `register_token`. This is realistic in any deployment where community- or partner-issued assets (common on Asset Hub-style chains) are bridged, since asset ownership and Hyper Fungible Token registration governance are separate authorities by design, and nothing in the pallet re-validates or freezes `decimals` post-registration. The attacker does not need any privileged bridge role — only ownership of the bridged asset's on-chain metadata, which is often held by third parties.

### Recommendation
Do not re-read `decimals()` live in `send`/`on_accept`/`on_timeout`. Instead, capture and store `local_decimals` alongside `erc_decimals` in `Precisions` (or a new storage item) at `register_token`/`update_token` time, and use that stored, immutable value for all subsequent conversions. Alternatively, re-validate at every `send` that the live `decimals()` still equals the value used at registration (erroring out if it drifted), and/or make the pallet the sole entity authorized to manage decimals metadata for registered assets (e.g. via `Freezer`/team controls) so it can't be changed independently of the bridge configuration.

### Proof of Concept
1. Register asset `X` (native or non-native) with `local_decimals = 10` at registration; `register_token` stores `Precisions[X][dest] = 18`.
2. As the asset's owner, call `pallet_assets::set_metadata(X, ..., decimals = 0)`.
3. Call `HyperFungibleToken::send({ asset_id: X, amount: 1_000_000_000_000, destination: dest, ... })`.
4. Observe: raw units burned/escrowed = `1_000_000_000_000`, but `convert_to_erc20(1_000_000_000_000, 18, 0)` = `1_000_000_000_000 * 10^18`, versus the registration-time-intended `1_000_000_000_000 * 10^8`Order-of-magnitude mismatch between the value actually removed from source custody and the value encoded into the outbound `Message.amount`, confirming inconsistency as described in the challenge's proof idea.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L251-290)
```rust
			let token_contract =
				TokenContracts::<T>::get(params.destination, params.asset_id.clone())
					.ok_or(Error::<T>::TokenContractNotFound)?;
			let erc_decimals = Precisions::<T>::get(params.asset_id.clone(), params.destination)
				.ok_or(Error::<T>::DecimalsNotFound)?;

			// Lock or burn the local asset
			let decimals = if params.asset_id == T::NativeAssetId::get() {
				// escrow the native asset
				<T as Config>::NativeCurrency::transfer(
					&who,
					&Self::pallet_account(),
					params.amount,
					ExistenceRequirement::AllowDeath,
				)?;
				T::Decimals::get()
			} else {
				let is_native = NativeAssets::<T>::get(params.asset_id.clone());
				if is_native {
					<T as Config>::Assets::transfer(
						params.asset_id.clone(),
						&who,
						&Self::pallet_account(),
						params.amount.into(),
						Preservation::Expendable,
					)?;
				} else {
					<T as Config>::Assets::burn_from(
						params.asset_id.clone(),
						&who,
						params.amount.into(),
						Preservation::Expendable,
						Precision::Exact,
						Fortitude::Polite,
					)?;
				}
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					params.asset_id.clone(),
				)
			};
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L336-368)
```rust
			let local_decimals = if registration.local_id == T::NativeAssetId::get() {
				T::Decimals::get()
			} else {
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					registration.local_id.clone(),
				)
			};

			NativeAssets::<T>::insert(registration.local_id.clone(), registration.native);

			let chains: Vec<StateMachine> = registration.chains.keys().cloned().collect();
			for (chain, config) in registration.chains {
				// This pallet bridges substrate <-> EVM only; reject non-EVM peers.
				if !matches!(chain, StateMachine::Evm(_)) {
					return Err(Error::<T>::NonEvmPeerChain.into());
				}
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
				let token_contract = config.token_contract.0.to_vec();
				TokenContracts::<T>::insert(
					chain,
					registration.local_id.clone(),
					token_contract.clone(),
				);
				ContractToAsset::<T>::insert(
					chain,
					token_contract,
					registration.local_id.clone(),
				);
				Precisions::<T>::insert(registration.local_id.clone(), chain, config.decimals);
			}
```

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L54-59)
```rust
/// Converts a local u128 balance to an ERC20 U256 amount
///
/// Multiplies by 10^(erc_decimals - local_decimals) to scale up to ERC20 precision
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
	U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```

**File:** parachain/runtimes/nexus/src/ismp.rs (L268-291)
```rust
impl pallet_assets::Config for Runtime {
	type RuntimeEvent = RuntimeEvent;
	type Balance = Balance;
	type AssetId = H256;
	type AssetIdParameter = H256;
	type Currency = Balances;
	type CreateOrigin = AsEnsureOriginWithArg<EnsureRootWithSuccess<AccountId32, TreasuryAccount>>;
	type ForceOrigin = EnsureRoot<AccountId32>;
	type AssetDeposit = AssetDeposit;
	type AssetAccountDeposit = AssetAccountDeposit;
	type MetadataDepositBase = MetadataDepositBase;
	type MetadataDepositPerByte = MetadataDepositPerByte;
	type ApprovalDeposit = ApprovalDeposit;
	type StringLimit = ConstU32<50>;
	type Freezer = ();
	type WeightInfo = weights::pallet_assets::WeightInfo<Runtime>;
	type CallbackHandle = ();
	type Extra = ();
	type RemoveItemsLimit = ConstU32<5>;
	type Holder = ();
	type ReserveData = ();
	#[cfg(feature = "runtime-benchmarks")]
	type BenchmarkHelper = XcmBenchmarkHelper;
}
```
