## Analog Identified: Asymmetric decimal scaling in `hyper-fungible-token` amount conversion

### Title
Saturating-subtraction decimal scaling in `convert_to_balance`/`convert_to_erc20` silently no-ops when local decimals exceed ERC20 decimals, causing wrong-amount minting/releasing across the bridge - (File: `modules/pallets/hyper-fungible-token/src/impls.rs`)

### Summary
This is the direct Hyperbridge analog of the `MagicLpAggregator` finding: the external bug is "a value that should be decimal-normalized isn't, causing an order-of-magnitude price error." Here, the `hyper-fungible-token` pallet's amount-normalization helpers assume one decimal ordering (ERC20 decimals ≥ local Substrate asset decimals) and silently degrade to a no-op scale factor when that assumption is violated, producing amounts that are off by `10^n`, exactly like the unscaled MagicLP share price.

### Finding Description
`convert_to_balance` and `convert_to_erc20` both compute their scale factor using `erc_decimals.saturating_sub(local_decimals)` (or vice versa), never the reverse direction: [1](#0-0) 

- `convert_to_balance` (used to credit incoming transfers in `on_accept` and refunds in `on_timeout`) divides the incoming ERC20 `U256` amount by `10^(erc_decimals.saturating_sub(local_decimals))`. This is only correct when `erc_decimals >= local_decimals`. When `local_decimals > erc_decimals` (e.g., a local asset registered with 18 decimals bridging a 6-decimal ERC20 like USDC), `saturating_sub` returns `0`, so the divisor is `1` — the raw ERC20 amount is minted/transferred unchanged into a balance space that expects `10^(local_decimals - erc_decimals)` more precision.
- `convert_to_erc20` (the symmetric outbound path) multiplies by `10^(erc_decimals.saturating_sub(local_decimals))` for the same reason, which also silently collapses to `×1` in that same decimal ordering.

The decimal values on both sides come from governance-configured, per-contract state — `Precisions::<T>::get(local_asset_id, source)` for the ERC20 side and `fungibles::metadata::Inspect::decimals` for the local side: [2](#0-1) 

Neither `on_accept` nor `on_timeout` validates the relative ordering of `erc_decimals` vs. `decimals` before calling `convert_to_balance`; the helper just silently mis-scales instead of reverting.

### Impact Explanation
If any registered local asset has more decimals than its counterpart ERC20 contract (a configuration that is entirely plausible — many Substrate assets use 12 or 18 decimals while common ERC20 stablecoins use 6), the outbound conversion (`convert_to_erc20`) will encode an amount that is `10^(local_decimals - erc_decimals)` times larger than intended when a user burns/locks tokens locally to bridge out. This causes the destination chain to release/mint a vastly inflated amount relative to what was actually escrowed — unauthorized minting / fund drain of the destination-side liquidity, directly matching the bounty's "false state acceptance / unauthorized execution / fund loss to wrong amount" criteria. Conversely, the inbound path (`convert_to_balance`) under-mints for users receiving funds, permanently trapping/losing value for legitimate recipients. Both directions are reachable purely by a normal user calling public bridge entrypoints — no relayer, prover, or admin compromise is required.

### Likelihood Explanation
This triggers deterministically whenever a governance-registered asset pair has `local_decimals > erc_decimals`, which is a realistic and common token-decimals configuration (e.g., wrapping a 6-decimal stablecoin into an 18-decimal Substrate asset). No attacker privilege beyond being a normal user of the bridge is needed to trigger the mis-scaled mint/release once such an asset pair exists.

### Recommendation
Replace the one-directional `saturating_sub` scaling with explicit bidirectional handling, mirroring the pattern already used correctly elsewhere in this codebase (e.g., `VWAPOracle._normalizeAmount` and `sdk/packages/sdk/src/utils.ts:adjustDecimals`, both of which branch on `<`, `==`, `>` rather than assuming one direction):

```rust
pub fn convert_to_balance<B: core::str::FromStr>(
    value: U256, erc_decimals: u8, local_decimals: u8,
) -> Result<B, B::Err> {
    let scaled = if erc_decimals >= local_decimals {
        value / U256::from(10u128.pow((erc_decimals - local_decimals) as u32))
    } else {
        value * U256::from(10u128.pow((local_decimals - erc_decimals) as u32))
    };
    scaled.to_string().parse::<B>()
}
```
Apply the symmetric fix to `convert_to_erc20`, and add a test asserting round-trip correctness for both decimal orderings (mirroring `VWAPOracleTest.sol::testHighDecimalsNormalization`).

### Proof of Concept
1. Governance registers a local asset `X` with `decimals() == 18` and configures `Precisions::<T>::insert(X, evm_contract, 6)` (the ERC20 side has 6 decimals — e.g., wrapping USDC-style token).
2. A user locks `1_000_000` raw units (i.e., `1.0` token at 18 decimals) of asset `X` and initiates an outbound bridge transfer, invoking the extrinsic that calls `convert_to_erc20(1_000_000_000_000_000_000u128, 6, 18)`.
3. Because `6 < 18`, `erc_decimals.saturating_sub(local_decimals) == 0`, so the multiplier is `10^0 = 1`, producing an outbound ERC20 amount of `1_000_000_000_000_000_000` — interpreted on the EVM side (6 decimals) as `1,000,000,000,000 USDC` instead of `1 USDC`.
4. The destination `on_accept` handler mints/releases this inflated amount to the beneficiary, draining the destination-side escrow/supply relative to what was actually locked on the source chain.

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

**File:** modules/pallets/hyper-fungible-token/src/module.rs (L74-91)
```rust
		// Convert amount from ERC20 denomination to local
		let decimals = if local_asset_id == T::NativeAssetId::get() {
			T::Decimals::get()
		} else {
			<T::Assets as fungibles::metadata::Inspect<T::AccountId>>::decimals(
				local_asset_id.clone(),
			)
		};
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
