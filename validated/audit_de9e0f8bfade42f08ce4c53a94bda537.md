## Finding

The bug-class in the external report is a broken 1:1 conservation invariant between the amount "locked/burned" on one side of a bridge and the amount "minted/unlocked" on the other side, caused by two independently-computed conversions that don't actually agree. In Hyperbridge's `pallet-hyper-fungible-token`, the exact same class of bug exists in the decimal-scaling helpers used to convert amounts between the Substrate side and the EVM side — except here it's not a timing/valuation race, it's a straight arithmetic bug in `saturating_sub` that silently drops the scaling factor whenever the EVM asset has fewer decimals than the local asset.

### Title
Broken decimal conversion in `convert_to_erc20`/`convert_to_balance` inflates amounts minted on the destination chain relative to what was burned/locked on the source - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
`pallet-hyper-fungible-token::send()` computes the cross-chain message amount via `convert_to_erc20(amount, erc_decimals, decimals)`, and the receiving side (`on_accept`/`on_timeout`) computes the local credit via `convert_to_balance(value, erc_decimals, local_decimals)`. Both helpers use `erc_decimals.saturating_sub(local_decimals)` to derive the scaling exponent. Whenever the local asset has *more* decimals than the registered EVM-side asset (`local_decimals > erc_decimals`), `saturating_sub` clamps to `0`, so the scaling factor becomes `10^0 = 1` — i.e., no scaling is applied at all, even though a real difference in precision exists between the two chains.

### Finding Description [1](#0-0) 

```
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

pub fn convert_to_erc20(value: u128, erc_decimals: u8, local_decimals: u8) -> U256 {
    U256::from(value) * U256::from(10u128.pow(erc_decimals.saturating_sub(local_decimals) as u32))
}
```

Both functions assume `erc_decimals >= local_decimals`. When that assumption is false (i.e., a token is registered with an EVM-side precision lower than the local Substrate asset's precision — e.g. an 18-decimal local asset bridged to a 6-decimal ERC6160/ERC20 representation), `erc_decimals.saturating_sub(local_decimals)` becomes `0` instead of the correct (negative, i.e. "divide" direction) exponent. The multiplier collapses to `1`, so `convert_to_erc20` fails to scale the value *down* by `10^(local_decimals - erc_decimals)` before embedding it in the outbound `Message.amount` field.

`send()` calls this directly: [2](#0-1) 

The local amount is escrowed/burned correctly at full local-decimal precision, but the erc20 amount placed in the ISMP `PostRequest` body is numerically identical to the raw local amount — which, once interpreted by the destination `HyperFungibleToken`/`WrappedHyperFungibleToken` contract as an amount denominated in the (smaller) `erc_decimals`, represents a value `10^(local_decimals - erc_decimals)` times larger than what was actually locked/burned.

### Impact Explanation
This breaks exactly the invariant the external report is about: the amount minted/unlocked on the destination chain no longer represents the amount burned/locked on the source chain 1:1. Unlike the ezETH case (a valuation-timing race that produces a modest, bounded discrepancy), this is a deterministic decimal-order-of-magnitude bug: any registration where `erc_decimals < local_decimals` lets every ordinary `send()` call mint `10^(local_decimals - erc_decimals)` times the intended value on the destination chain — enabling essentially unlimited over-minting of bridged funds from a small locked/burned amount. This is unauthorized value creation / loss of funds at the protocol level, squarely inside the bounty's "stealing or loss of funds" and "logic attacks" categories. The mirrored `convert_to_balance` bug (used in `on_accept`/`on_timeout`) causes silent under-crediting in the reverse decimal relationship, permanently locking user funds.

### Likelihood Explanation
No privileged, malicious, or off-chain actor is required — only a governance-configured asset (via `register_token`/`update_token`, which is a normal, expected configuration path, not an attack) where the destination's `Precisions` value is lower than the local asset's decimals. Any ordinary signed user then triggers the bug simply by calling `send()`. Because decimal mismatches across chains are the norm rather than the exception (native chain assets are frequently higher-precision than their EVM ERC-20 counterparts, e.g. 18 vs 6), this configuration is very likely to arise in real deployments.

### Recommendation
Fix both helpers to handle both directions of the decimal difference explicitly, e.g.:
```rust
if erc_decimals >= local_decimals {
    value * 10u128.pow((erc_decimals - local_decimals) as u32)
} else {
    value / 10u128.pow((local_decimals - erc_decimals) as u32)
}
```
applied symmetrically in `convert_to_erc20` and `convert_to_balance`, and add regression tests that specifically register a token with `erc_decimals < local_decimals` and assert that the amount conserved across a full send/receive round trip.

### Proof of Concept
1. Governance registers an asset via `register_token` where the local asset has 18 decimals (`Assets::decimals(asset_id) == 18`) and the EVM chain's precision is recorded as `Precisions::<T>::get(asset_id, dest) == 6` (a plausible, realistic configuration for bridging to a 6-decimal ERC20 representation).
2. A user calls `send(params)` with `amount = 1_000_000_000_000_000_000` (i.e., `1.0` token at 18 decimals).
3. Inside `send()`: `decimals = 18`, `erc_decimals = 6`. `convert_to_erc20(1e18, 6, 18)` computes `10u128.pow(6u8.saturating_sub(18) as u32) = 10u128.pow(0) = 1`, so `erc20_amount = 1e18` — unchanged, instead of the correct `1e18 / 10^12 = 1e6`.
4. The outbound `Message.amount` field therefore carries `1_000_000_000_000_000_000` raw units, which the destination `HyperFungibleToken` contract (using 6 decimals) interprets as `1,000,000,000,000` tokens (1 trillion tokens) instead of `1` token.
5. The user has burned/escrowed only 1 local token but receives (or a recipient receives) 10^12 times that value on the destination chain — a straightforward, deterministic mint-inflation bug, confirmed purely from the arithmetic in [3](#0-2) .

### Citations

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

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L254-302)
```rust
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

			// Encode the Message body
			let sender: [u8; 32] = who.clone().into();
			let amount: u128 = params.amount.into();
			let erc20_amount = convert_to_erc20(amount, erc_decimals, decimals);

			let token_message = Message {
				from: sender.to_vec().into(),
				to: params.recipient.to_vec().into(),
				amount: alloy_primitives::U256::from_be_bytes(erc20_amount.to_big_endian()),
				data: params.call_data.unwrap_or_default().into(),
			};
```
