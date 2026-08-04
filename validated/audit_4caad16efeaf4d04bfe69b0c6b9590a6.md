## Finding

### Title
`convert_to_erc20`/`convert_to_balance` silently drop decimal scaling via `saturating_sub` when local decimals exceed remote decimals, enabling cross-chain mint/release amount inflation - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
This is the direct Hyperbridge analog of the `CompoundV2Connector#_convertSharesToAssets` bug: a decimal-magnitude formula that assumes one ordering of decimals (`erc_decimals >= local_decimals`) and silently produces a wrong scale factor instead of reverting when that assumption is violated, causing amounts to be off by many orders of magnitude.

### Finding Description
`convert_to_erc20` and `convert_to_balance` compute the decimal-scaling exponent with `saturating_sub`: [1](#0-0) 

```rust
pub fn convert_to_balance<B: core::str::FromStr>(
    value: U256, erc_decimals: u8, local_decimals: u8,
) -> Result<B, B::Err> {
    let dec_str = (value /
        U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32)))
    .to_string();
    dec_str.parse::<B>()
}

pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
    U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```

Both functions assume `erc_decimals >= local_decimals`. When that assumption breaks (`local_decimals > erc_decimals`), `saturating_sub` clamps the exponent to `0` instead of applying the correct *downward* scale (division), so the functions silently multiply/divide by `10^0 = 1` — exactly the same class of bug as the reported Compound issue, where an incorrect magnitude produced a value off by `10^10`.

`send()` calls `convert_to_erc20(amount, erc_decimals, decimals)` unconditionally at dispatch time, using `decimals` read *live* from the asset's own metadata via `<T::Assets as fungibles::metadata::Inspect>::decimals(asset_id)`: [2](#0-1) 

The pallet defines an `ErcDecimalsBelowLocal` error explicitly acknowledging this invariant must hold: [3](#0-2) 

However, that check (if enforced at all) can only run at `register_token`/`update_asset_precision` time — the `Precisions` (remote `erc_decimals`) is fixed governance-controlled storage, but `decimals` for non-native assets is read live from `pallet-assets` metadata, which the asset's `Owner` (the account that created the asset in `pallet-assets`, not necessarily the bridging governance `CreateOrigin`) can update at any later time via `pallet_assets::set_metadata`. Registration in this pallet ("must already exist in pallet-assets") does not transfer or freeze that ownership.

### Impact Explanation
If an asset owner (an ordinary, unprivileged account with respect to the bridge) raises their asset's local decimals above the `erc_decimals` value already registered for a destination chain, every subsequent `send()` for that asset skips the down-scale division entirely. The wire `Message.amount` transmitted to the destination `HyperFungibleToken`/`WrappedHyperFungibleToken` contract becomes `10^(local_decimals - erc_decimals)` times larger than the correctly-scaled value, while only the small, correctly-denominated `amount` was actually escrowed/burned locally. The destination contract will mint or release tokens vastly in excess of what was locked/burned on the source chain — a direct false-state/over-mint fund-loss primitive matching the bounty's "false proof/state acceptance" and "stealing or loss of funds" categories. The inverse direction (`convert_to_balance`, used on `on_accept`/receipt) has the mirrored failure: when `local_decimals > erc_decimals`, no up-scale is applied on receipt, truncating credited balances and permanently locking funds for legitimate senders.

### Likelihood Explanation
The trigger requires only an ordinary account that (a) created the underlying `pallet-assets` asset before/independently of its bridge registration, and (b) retains `set_metadata` rights over its own asset's decimals after governance registers it for bridging — both are standard, non-privileged pallet-assets permissions, not a compromised relayer/prover/admin. No malicious peer, governance action, or leaked key is required; the attacker only needs to own an asset that ends up registered for hyper-fungible-token bridging and to change its decimals afterward.

### Recommendation
Re-validate `erc_decimals >= local_decimals` inside `convert_to_erc20`/`convert_to_balance` themselves (or immediately before calling them in `send()`/`on_accept`), returning `Error::ErcDecimalsBelowLocal` rather than relying on a registration-time-only check against metadata that can change later. Alternatively, snapshot and freeze the local asset's decimals in `Precisions`/pallet storage at registration time instead of reading live, mutable `pallet-assets` metadata on every `send()`.

### Proof of Concept
1. Attacker creates asset `X` in `pallet-assets` with `decimals = 6`.
2. Governance (`CreateOrigin`) registers `X` for bridging to chain `EVM-1` via `register_token`, setting `Precisions::<T>::get(X, EVM-1) = 6` (erc_decimals = 6), passing any decimals check that only runs once at registration.
3. Attacker, still the asset's metadata `Owner`, calls `pallet_assets::set_metadata(X, ..., decimals: 24)`.
4. Attacker calls `send(SendParams { asset_id: X, amount: 1_000 (raw units), destination: EVM-1, ... })`.
   - `decimals` is read live as `24`; `erc_decimals = 6`.
   - `convert_to_erc20(1000, 6, 24)` computes exponent `6.saturating_sub(24) = 0`, so `erc20_amount = 1000 * 10^0 = 1000`.
   - Correct value should have been `1000 / 10^18 ≈ 0` (essentially dust) — instead the destination contract is told to mint/release `1000` units at its expected 6-decimal ERC scale, i.e. `0.001` real tokens worth becomes `1000 / 10^6 = 0.001` — but scale this up: if attacker instead burns `X` worth `1000 * 10^24` raw units (i.e., 1000 real tokens, cheap/available balance), `convert_to_erc20` still applies no division, sending `erc20_amount = 1000 * 10^24`, which the destination interprets at 6 decimals as `10^21` tokens — an 18-order-of-magnitude inflation matching the Compound-style off-by-`10^n` defect. [4](#0-3)

### Citations

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L43-58)
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

/// Converts a local u128 balance to an ERC20 U256 amount
///
/// Multiplies by 10^(erc_decimals - local_decimals) to scale up to ERC20 precision
pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
	U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L221-223)
```rust
		/// Configured ERC decimals are less than the local asset's decimals; precision conversion
		/// requires erc_decimals >= local_decimals
		ErcDecimalsBelowLocal,
```

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L267-296)
```rust
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
