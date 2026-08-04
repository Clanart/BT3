## Title
Live re-fetch of asset `decimals()` in `on_accept`/`on_timeout` lets an unprivileged asset owner drift decimals between escrow and settlement to mint/drain more tokens than escrowed - (File: `modules/pallets/hyper-fungible-token/src/module.rs`)

### Summary
`Pallet::send`, `on_accept`, and `on_timeout` in the `hyper-fungible-token` pallet each independently re-query the *live* metadata decimals of the local asset (`<T::Assets as fungibles::metadata::Inspect<..>>::decimals(...)`) instead of using the decimals value that was fixed at the time the corresponding cross-chain `send` was dispatched. Because `pallet_assets::Config::CreateOrigin` in the runtime is permissionless (`AsEnsureOriginWithArg<EnsureSigned<AccountId32>>`), any unprivileged user can create their own asset and, as its `Owner`, freely call `set_metadata` at any time to change its `decimals` field — there is no `Freezer` enforcing immutability. This lets an attacker who owns the bridged asset change its decimals between the initial `send()` escrow and the later `on_timeout`/`on_accept` settlement, corrupting the ERC20⇄local decimal conversion and causing the pallet to refund/mint far more local tokens than were ever escrowed.

### Finding Description
`send()` converts the escrowed local amount to ERC20 precision using the *current* decimals at send time: [1](#0-0) 

This `erc20_amount` (fixed number, embedded in the outbound `Message.amount`) is transmitted cross-chain. Later, `on_timeout` (and `on_accept`) decode this same fixed ERC20 amount and convert it back using a **freshly re-fetched** decimals value: [2](#0-1) 

The conversion itself divides by `10^(erc_decimals - local_decimals)`: [3](#0-2) 

If `local_decimals` at settlement time is larger than at send time, the divisor shrinks (or becomes `1` via `saturating_sub` once `local_decimals` exceeds `erc_decimals`), so the recovered `amount` becomes inflated by a factor of `10^(decimals_timeout - decimals_send)` relative to what was actually escrowed.

The decimals value is never pinned to the asset at `register_token`/`send` time — it is re-derived live every time: [4](#0-3) [5](#0-4) 

`register_token`'s only guard (`config.decimals >= local_decimals`) is checked once, at registration, and is not re-validated or enforced afterward: [6](#0-5) 

Crucially, the underlying asset's decimals metadata is owned by whoever created the asset in `pallet_assets`, and asset creation there is permissionless for any signed account, with no `Freezer`: [7](#0-6) 

`register_token`/`update_token` themselves require `EnsureRoot` (`CreateOrigin`), which only gates the *bridge mapping*, not the asset's own metadata mutability: [8](#0-7) 

So an attacker can: (1) permissionlessly create an asset with low decimals, (2) have governance register it for bridging (a normal onboarding step, not attacker-controlled but not preventing the later attack), (3) `send()` tokens to escrow them in `pallet_account` (or burn them, if `is_native == false`), (4) call `pallet_assets::set_metadata` to raise the asset's decimals, and (5) force/await a timeout so `on_timeout` re-reads the higher decimals and refunds an amplified amount — either draining `pallet_account`'s shared custody balance (if the asset is "native", i.e. transferred rather than minted) or minting excess new supply (if not native).

### Impact Explanation
This lets an unprivileged attacker drain the pallet's custodial escrow account (`pallet_account`) funds meant for other bridge users, or mint unbacked local asset supply, purely by mutating metadata they legitimately control on their own asset — a direct "stealing or loss of funds" / "wrong amount" / "broken refund accounting" impact matching the bounty's required impact categories.

### Likelihood Explanation
High. Asset creation and `set_metadata` calls in `pallet_assets` are ordinary unprivileged extrinsics available to any signed account acting as the asset `Owner`. No code in the reviewed paths pins, snapshots, or re-validates decimals between `send` and settlement (`on_accept`/`on_timeout`), and no `Freezer` is configured to prevent metadata mutation.

### Recommendation
Snapshot and persist the local asset's decimals (alongside `erc_decimals` in `Precisions`, or in a per-request record keyed by commitment) at the time of `send()`, and use that pinned value in `on_accept`/`on_timeout` rather than re-querying live `Inspect::decimals`. Alternatively, enforce metadata immutability (via `Freezer`) for any asset once registered for bridging, or reject decimals changes for bridged assets in `register_token`/`update_token` continuously (not just once at registration).

### Proof of Concept
1. Attacker calls `pallet_assets::create` (permissionless `CreateOrigin`) to create asset `X` with `decimals = 6`, becoming its `Owner`.
2. Governance calls `hyper_fungible_token::register_token` mapping asset `X` to an EVM contract with `erc_decimals = 18` (passes the `config.decimals >= local_decimals` check: `18 >= 6`).
3. Attacker calls `send()` with `amount = 100` local units. `decimals` fetched = 6, so `erc20_amount = 100 * 10^(18-6) = 100 * 10^12`. Tokens transferred to `pallet_account` (assuming `is_native = true`).
4. Before the timeout window elapses (or before the timeout proof settles), attacker calls `pallet_assets::set_metadata` on asset `X` raising `decimals` to `12`.
5. The request times out; `on_timeout` re-fetches decimals = 12, and computes `amount = erc20_amount / 10^(18-12) = 100*10^12 / 10^6 = 100*10^6`, i.e. a refund `10^6` times larger than what was originally escrowed.
6. Assert: refunded amount (`100 * 10^6`) is drastically greater than escrowed amount (`100`), draining `pallet_account`'s shared balance backing other users' escrows.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L287-295)
```rust
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					params.asset_id.clone(),
				)
			};

			// Encode the Message body
			let sender: [u8; 32] = who.clone().into();
			let amount: u128 = params.amount.into();
			let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L336-342)
```rust
			let local_decimals = if registration.local_id == T::NativeAssetId::get() {
				T::Decimals::get()
			} else {
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					registration.local_id.clone(),
				)
			};
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L350-355)
```rust
					return Err(Error::<T>::NonEvmPeerChain.into());
				}
				ensure!(
					config.decimals >= local_decimals,
					Error::<T>::ErcDecimalsBelowLocal
				);
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L75-81)
```rust
		let decimals = if local_asset_id == T::NativeAssetId::get() {
			T::Decimals::get()
		} else {
			<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
				local_asset_id.clone(),
			)
		};
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L239-255)
```rust
				let decimals = if local_asset_id == T::NativeAssetId::get() {
					T::Decimals::get()
				} else {
					<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
						local_asset_id.clone(),
					)
				};
				let erc_decimals = Precisions::<T>::get(local_asset_id.clone(), dest)
					.ok_or(HftError::DecimalsNotConfigured(dest))?;
				let amount = convert_to_balance::<
					<<T as Config>::NativeCurrency as Currency<T::AccountId>>::Balance,
				>(
					U256::from_big_endian(&message.amount.to_be_bytes::<32>()),
					erc_decimals,
					decimals,
				)
				.map_err(|e| HftError::InvalidAmountConversion(format!("{e:?}")))?;
```

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L43-52)
```rust
pub fn convert_to_balance<B: core::str::FromStr>(
	value: U256,
	erc_decimals: u8,
	local_decimals: u8,
) -> Result<B, B::Err> {
	let dec_str = (value /
		U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32)))
	.to_string();
	dec_str.parse::<B>()
}
```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L288-299)
```rust
impl pallet_hyper_fungible_token::Config for Runtime {
	type Dispatcher = Ismp;
	type Assets = Assets;
	type NativeCurrency = Balances;
	type NativeAssetId = HftNativeAssetId;
	type CreateOrigin = EnsureRoot<AccountId>;
	type Decimals = HftDecimals;
	type EvmToSubstrate = ();
	type WeightInfo = crate::weights::pallet_hyper_fungible_token::WeightInfo<Runtime>;
	#[cfg(feature = "runtime-benchmarks")]
	type BenchmarkHelper = HftBenchmarkHelper;
}
```

**File:** parachain/runtimes/gargantua/src/ismp.rs (L350-373)
```rust
impl pallet_assets::Config for Runtime {
	type RuntimeEvent = RuntimeEvent;
	type Balance = Balance;
	type AssetId = H256;
	type AssetIdParameter = H256;
	type Currency = Balances;
	type CreateOrigin = AsEnsureOriginWithArg<frame_system::EnsureSigned<AccountId32>>;
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
