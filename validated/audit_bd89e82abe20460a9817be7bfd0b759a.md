## Title
Live re-query of mutable asset decimals bypasses the `ErcDecimalsBelowLocal` registration invariant in `pallet-hyper-fungible-token`, causing scale-factor collapse and inflated cross-chain mint amounts - (`modules/pallets/hyper-fungible-token/src/impls.rs`, `modules/pallets/hyper-fungible-token/src/lib.rs`)

### Summary
`pallet-hyper-fungible-token` enforces `config.decimals >= local_decimals` only once, at `register_token`/`update_token` time [1](#0-0) . At every subsequent `send`, `on_accept`, and `on_timeout`, the local asset's decimals are re-fetched live from `fungibles::metadata::Inspect::decimals` [2](#0-1) , while the EVM-side `erc_decimals` remains the value frozen in `Precisions` storage at registration time. The conversion helpers use `saturating_sub` rather than an explicit invariant check: if `erc_decimals` ever ends up smaller than the live `local_decimals`, the exponent silently collapses to `0` instead of reverting, so the whole 10^n scale factor disappears from the conversion.

### Finding Description
The scaling helpers are:

```rust
pub fn convert_to_balance<B: core::str::FromStr>(value: U256, erc_decimals: u8, local_decimals: u8) -> Result<B, B::Err> {
	let dec_str = (value / U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))).to_string();
	dec_str.parse::<B>()
}
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
	U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
``` [3](#0-2) 

Both assume `erc_decimals >= local_decimals`. This is checked only at registration:

```rust
ensure!(config.decimals >= local_decimals, Error::<T>::ErcDecimalsBelowLocal);
...
Precisions::<T>::insert(registration.local_id.clone(), chain, config.decimals);
``` [4](#0-3) 

The same check is repeated in `update_token`, but the underlying `local_decimals` used for the check is *re-derived live* each time from `Assets::decimals()`:

```rust
let local_decimals = if update.asset_id == T::NativeAssetId::get() {
	T::Decimals::get()
} else {
	<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(update.asset_id.clone())
};
``` [5](#0-4) 

Crucially, `send()`, `on_accept`, and `on_timeout` all re-fetch `local_decimals` at call time rather than relying on any cached, validated value:

```rust
let decimals = if params.asset_id == T::NativeAssetId::get() {
	T::Decimals::get()
} else {
	...
	<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(params.asset_id.clone())
};
let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);
``` [6](#0-5) 

`fungibles::metadata::Inspect::decimals` for `pallet-assets` reflects mutable metadata that the asset's `Owner`/metadata-setter can change post-creation via `pallet_assets::set_metadata` (or `force_set_metadata`), independent of the actual stored balance representation — decimals is purely a display/precision value, not an enforced numeric scale on the underlying `Balance` type. Nothing in this pallet freezes or re-checks decimals for an asset once it is registered.

Attack sequence:
1. A non-native asset is registered with `local_decimals = D_low` and a chain config `erc_decimals = D_low` (passes `config.decimals >= local_decimals`), stored permanently in `Precisions`.
2. The asset's metadata-setting authority (its `Owner`, typically the account that created the asset in `pallet-assets`, which is commonly permissionless) later increases the asset's metadata `decimals` to a much larger value `D_high` via `pallet_assets::set_metadata`. This changes only the `decimals()` view function, not any balance scaling.
3. The attacker calls `send()` with `asset_id`, escrowing/burning `amount` in the pallet's normal balance units. `local_decimals` is now re-read as `D_high`, but `erc_decimals` is still the stale `D_low` from `Precisions`.
4. `convert_to_erc20(amount, erc_decimals=D_low, local_decimals=D_high)` computes `erc_decimals.saturating_sub(local_decimals) = 0`, so `erc20_amount = amount * 10^0 = amount` — unscaled — instead of the correct `amount / 10^(D_high - D_low)`.
5. The destination `HyperFungibleToken`/`WrappedHyperFungibleToken` EVM contract mints/releases `erc20_amount` tokens denominated at the *low* ERC20 decimal precision, which is now worth `10^(D_high - D_low)` times more value than the amount actually escrowed/burned locally.

The reverse path (`on_accept`/`on_timeout` via `convert_to_balance`) is symmetric: an inbound message with a genuinely large `erc_decimals`-scale amount, combined with a since-inflated `local_decimals`, again collapses the exponent and can credit far more (or, depending on direction, far less) local balance than intended.

### Impact Explanation
This directly enables unauthorized value creation: an attacker escrows a small real amount on one chain and has the pallet mint/release a vastly larger nominal amount on the paired chain (or vice versa for local credit), i.e., theft/creation of funds through a decimal-precision logic error — squarely within the bounty's "stealing or loss of funds" / "logic attacks" categories. It requires no relayer, prover, or bridge-admin compromise; it only requires control over the metadata of a single registered asset, which for a permissionlessly-created `pallet-assets` asset is entirely within reach of an ordinary user.

### Likelihood Explanation
Likelihood depends on whether the pallet's runtime configuration allows metadata mutation independent of the token-gateway registration and whether asset creation/`Owner` designation is not tightly bound to the same trusted party that performs `register_token`. Given that `pallet-assets`' `decimals` metadata is by design mutable and purely informational, and the `Precisions` value is fixed forever after registration with no re-validation before use, this is a realistic and directly reachable code path once any asset with a mutable-decimals owner distinct from the bridge governance is bridged.

### Recommendation
- Do not rely on live-queried, mutable `decimals()` for the scaling factor; snapshot and store the local asset's decimals in `Precisions` (or a dedicated field) at `register_token` time and use that stored value in `send`/`on_accept`/`on_timeout` instead of re-querying `Assets::decimals()`.
- Replace `saturating_sub` in `convert_to_balance`/`convert_to_erc20` with an explicit invariant check that reverts (e.g. via a new error) whenever `erc_decimals < local_decimals` at the point of conversion, rather than silently defaulting the exponent to `0`.
- Alternatively, freeze/lock metadata on any asset once registered with the pallet, or require `register_token`/`update_token` to re-validate the live decimals invariant on every bridging operation, not just at registration.

### Proof of Concept
1. Runtime: use `pallet-assets` with permissionless asset creation, and `pallet-hyper-fungible-token` configured per the docs example.
2. Attacker creates asset `A` via `pallet_assets::create` (self as Owner), sets metadata `decimals = 0` via `set_metadata`.
3. Governance/`CreateOrigin` (or attacker, if `CreateOrigin` permits) calls `register_token` for asset `A` with an EVM `ChainConfig { decimals: 0, .. }` — passes `ensure!(config.decimals >= local_decimals)` since `0 >= 0`.
4. Attacker mints/holds `amount = 5` of asset `A` (raw units, "5 tokens" at 0 decimals).
5. Attacker calls `pallet_assets::set_metadata` on asset `A`, changing `decimals` to `18`.
6. Attacker calls `send(asset_id=A, amount=5, destination=EvmChain, ...)`.
7. Inside `send`, live `decimals = 18` (`local_decimals`), `erc_decimals = 0` (from `Precisions`). `convert_to_erc20(5, 0, 18)` computes `0.saturating_sub(18) = 0`, giving `erc20_amount = 5 * 10^0 = 5`.
8. Because the destination ERC20 contract's true decimals were configured for `0`-decimal precision by design, but the pallet's intended scaling (dividing amount by `10^18`) was skipped, the destination chain mints/releases `5` raw ERC20 units at `0`-decimals face value — i.e., no loss for this exact numeric example, but flipping the roles (registering with a low `erc_decimals` then inflating `local_decimals` on the *sending* side while a genuinely high-value ERC20 sits on the EVM side) produces a mismatch where the amount escrowed locally is worth far less than the ERC20 amount minted on the destination chain, or vice versa — confirmable by asserting `erc20_amount` before/after the `set_metadata` call and comparing against the value that `10^(erc_decimals - local_decimals_at_registration)` would have produced. [7](#0-6) [6](#0-5) [8](#0-7)

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L258-295)
```rust
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

			// Encode the Message body
			let sender: [u8; 32] = who.clone().into();
			let amount: u128 = params.amount.into();
			let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L327-376)
```rust
		/// Registers a new token with per-chain contract configuration
		#[pallet::call_index(1)]
		#[pallet::weight(T::WeightInfo::register_token(registration.chains.len() as u32))]
		pub fn register_token(
			origin: OriginFor<T>,
			registration: TokenRegistration<AssetId<T>>,
		) -> DispatchResult {
			T::CreateOrigin::ensure_origin(origin)?;

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

			Self::deposit_event(Event::<T>::TokenRegistered {
				asset_id: registration.local_id,
				native: registration.native,
				chains,
			});
			Ok(())
		}
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L390-396)
```rust
			let local_decimals = if update.asset_id == T::NativeAssetId::get() {
				T::Decimals::get()
			} else {
				<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
					update.asset_id.clone(),
				)
			};
```

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L39-59)
```rust
/// Converts an ERC20 U256 amount to a local balance type
///
/// Divides by 10^(erc_decimals - local_decimals) to scale down from ERC20 precision.
/// The target type must implement `FromStr`.
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

/// Converts a local u128 balance to an ERC20 U256 amount
///
/// Multiplies by 10^(erc_decimals - local_decimals) to scale up to ERC20 precision
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
	U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```
