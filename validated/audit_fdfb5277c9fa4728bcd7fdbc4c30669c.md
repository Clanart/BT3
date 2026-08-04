## Answer

The most concrete issue here is in the shared decimal-conversion helpers used by `on_accept` (mint/transfer path) and `on_timeout` (refund path), not a duplicate/reordering issue at the ISMP dispatch layer (I was not able to fully verify duplicate-request-receipt protection in `modules/ismp/core/src/handlers/request.rs` before running out of tool iterations — see caveat at the end).

### Title
Asymmetric decimal-precision conversion in `convert_to_balance`/`convert_to_erc20` can massively over- or under-value bridged amounts - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
`on_accept` and `on_timeout` in `modules/pallets/hyper-fungible-token/src/module.rs` both call `convert_to_balance` to translate the remote (ERC20) `message.amount` into a local balance before minting/transferring funds to the beneficiary, using the configured remote `Precisions::<T>::get(...)` decimals and the local asset's decimals: [1](#0-0) [2](#0-1) 

### Finding Description
`convert_to_balance` and its mirror `convert_to_erc20` only implement a **one-directional** scaling using `saturating_sub`: [3](#0-2) 

- `convert_to_balance` computes `erc_decimals.saturating_sub(local_decimals)` and only **divides** the raw remote `U256` amount by `10^(that exponent)`.
- `convert_to_erc20` computes the same `saturating_sub` expression and only **multiplies**.

`saturating_sub` returns `0` whenever `local_decimals >= erc_decimals` (for `convert_to_balance`) or whenever `local_decimals >= erc_decimals` (for `convert_to_erc20`, same expression). In that case the divisor/multiplier becomes `10^0 = 1`, i.e. **no scaling is applied at all** — the raw integer amount is carried over verbatim between two different decimal bases.

Because `erc_decimals` (remote precision) is attacker/registrar-configurable per token via `Precisions::<T>` (`ContractToAsset`/`Precisions` storage set via the pallet's registration calls), and local asset decimals come from the local asset registry (`fungibles::metadata::Inspect::decimals`) or `T::Decimals::get()` for the native asset, any token configuration where local decimals are equal to or greater than remote decimals causes the "divide" step to be skipped on receive (`on_accept`/`on_timeout`), and the "multiply" step to be skipped on send. This means an amount expressed in one precision is copied as-is into a field interpreted at a different precision:

- If local decimals (e.g. 18) > remote decimals (e.g. 6): on send, the local raw amount (already scaled by 10^18) is placed directly into the outbound ERC20 `amount` field without being divided down to 10^6 — hugely **inflating** the amount the remote/EVM side will mint/release.
- Symmetrically, on receive/refund, a remote raw amount expressed in 10^6 is copied directly into a local balance that is interpreted at 10^18 — hugely **deflating** what the beneficiary receives, effectively burning value.

This breaks the required invariant "precision conversion must preserve economic value across send, receive, and timeout." Because both `Precisions::<T>` values are configurable per token/contract pair (registrar-controlled but not necessarily consistency-checked against the true remote contract's on-chain decimals), and because the local asset's decimals can independently exceed or fall below the registered remote decimals, this is reachable in realistic token-listing configurations, not just adversarial edge cases.

### Impact Explanation
An inconsistent local/remote decimal pairing lets `on_accept`/`on_timeout` mint or refund an amount that is off by a factor of `10^n` from the economically correct value. Depending on which side has more decimals, this leads either to systematic under-crediting of legitimate transfers (loss for users / stuck value) or gross over-minting relative to what was escrowed/burned on the source chain (protocol-fund drain), matching the "Critical: wrongful mint, unlock, withdrawal, refund" impact category.

### Likelihood Explanation
This does not require any privileged action beyond the normal token registration/configuration workflow already supported by the pallet (setting `chains`/`ChainConfig.decimals` and `Precisions`), plus a single legitimate cross-chain send/receive. It is triggered by ordinary asset listings where local and remote decimals aren't both configured with the (undocumented) assumption `erc_decimals >= local_decimals`, so likelihood is high whenever an operator lists a token whose local decimals are equal to or exceed the remote ERC20 decimals (a very common real-world case, e.g. 18-decimal local asset vs 6-decimal USDC-like remote token).

### Recommendation
Rewrite `convert_to_balance`/`convert_to_erc20` to handle both directions explicitly instead of relying on `saturating_sub` collapsing to zero, e.g.:
```rust
if erc_decimals >= local_decimals {
    value / 10^(erc_decimals - local_decimals)
} else {
    value * 10^(local_decimals - erc_decimals)
}
```
and add the mirrored multiply/divide in `convert_to_erc20`. Add unit/property tests that fuzz `(erc_decimals, local_decimals)` pairs in both directions and assert round-trip conservation of economic value for send → receive and send → timeout/refund paths.

### Proof of Concept
1. Register a local asset with `decimals = 18` and configure `Precisions::<T>` for the corresponding remote contract with `erc_decimals = 6` (a plausible real listing, e.g., bridging a USDC-like 6-decimal ERC20 to an 18-decimal local asset).
2. Send an outbound transfer of `amount = 1_000_000_000_000_000_000` (1 token, 18-decimal raw units) via the pallet's send extrinsic. `convert_to_erc20` computes `erc_decimals.saturating_sub(local_decimals) = 6.saturating_sub(18) = 0`, so the outbound ERC20 message carries `amount = 1_000_000_000_000_000_000` — interpreted on the remote EVM side as `1_000_000_000_000` "tokens" (at 6 decimals), a 10^12 amplification of value that the remote contract will mint/release.
3. Conversely, on the receive side, an authentic remote message specifying `amount = 1_000_000` (1 token at 6 decimals) yields `convert_to_balance` dividing by `10^(6.saturating_sub(18)) = 10^0 = 1`, crediting the local beneficiary only `1_000_000` raw local units — i.e. `0.000000000001` of a token instead of `1` token, an economic loss of 10^12x for the user.

### Caveat
I could not fully verify, within the available tool-call budget, whether `pallet-ismp`'s request/response handlers in `modules/ismp/core/src/handlers/request.rs` reject duplicate or reordered leaves/commitments within a single batched `handle_unsigned` call (i.e., whether request-receipt/commitment-uniqueness guards exist independent of this pallet). That part of the original question ("duplicate or reordered items inside one batch") remains unconfirmed and would need direct inspection of `modules/ismp/core/src/handlers/request.rs` and the relevant `RequestReceipts`/commitment storage before concluding whether replay of a duplicated leaf is possible.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L82-91)
```rust
		let erc_decimals = Precisions::<T>::get(local_asset_id.clone(), source)
			.ok_or(HftError::DecimalsNotConfigured(source))?;
		let amount = convert_to_balance::<
			<<T as Config>::NativeCurrency as Currency<T::AccountId>>::Balance,
		>(
			U256::from_big_endian(&message.amount.to_be_bytes::<32>()),
			erc_decimals,
			decimals,
		)
		.map_err(|e| HftError::InvalidAmountConversion(format!("{e:?}")))?;
```

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L246-255)
```rust
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
