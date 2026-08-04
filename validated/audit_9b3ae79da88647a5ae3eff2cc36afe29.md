## Analysis

The core broken invariant in the Sense report is: *code silently assumes underlying-decimals and PT-decimals always relate one specific way (equal, or IBT≥underlying), and does the wrong arithmetic (or none) in the opposite case, freezing/mispricing funds instead of erroring.*

The local analog is in the `hyper-fungible-token` pallet's decimal-conversion helpers, used on both the incoming-mint and outgoing-dispatch paths for bridged ERC20/native assets. [1](#0-0) 

### Title
Silent decimal-scaling failure when local asset decimals exceed ERC20 decimals causes fund loss/inflation in `hyper-fungible-token` - (File: modules/pallets/hyper-fungible-token/src/impls.rs)

### Summary
`convert_to_balance` and `convert_to_erc20` compute the scaling factor as `10u128.pow(erc_decimals.saturating_sub(local_decimals))`, which is only correct when `erc_decimals >= local_decimals`. The `Precisions` map that stores `erc_decimals` per asset/contract is set by governance with no invariant enforced that `erc_decimals >= local_decimals` for every configured asset. When a local asset is configured with more decimals than its bridged ERC20 counterpart (e.g. local asset uses 18 decimals while the mapped ERC20 token uses 6, like USDC), `saturating_sub` returns `0`, the divisor/multiplier collapses to `10^0 = 1`, and no decimal adjustment is applied at all — exactly the "assume one direction, silently do nothing in the other" failure pattern from the Sense report.

### Finding Description
`convert_to_balance` is invoked in `on_accept` (incoming mint/transfer to beneficiary) and `on_timeout` (refund) to translate the ERC20-encoded `U256` amount into the local balance: [2](#0-1) 

and identically in `on_timeout`: [3](#0-2) 

If `local_decimals (e.g. 18) > erc_decimals (e.g. 6)`, `erc_decimals.saturating_sub(local_decimals) == 0`, so the raw ERC20-precision integer (e.g. `1000 * 10^6` for 1000 USDC) is minted/transferred verbatim as the local balance, instead of being scaled up by `10^12` to match the local asset's 18-decimal precision. The beneficiary receives `1e9` raw local units instead of `1e21` — a ~10^12x under-crediting, i.e. funds are effectively lost/frozen for the recipient exactly like the Sense redeem underpayment case.

The mirror function, `convert_to_erc20`, is documented as multiplying "to scale up to ERC20 precision," again assuming `erc_decimals >= local_decimals`: [4](#0-3) 

Used on the outgoing dispatch path (converting a local balance being locked/burned into the ERC20-scaled amount embedded in the cross-chain message), the same `saturating_sub` collapse means that when `local_decimals > erc_decimals`, the full local-precision integer is embedded directly as the message amount without being scaled *down*. The destination EVM contract will then interpret that oversized integer as an ERC20/native amount to release/mint, producing an amount many orders of magnitude larger than what was actually escrowed/burned on the source chain — an unauthorized-amount/value-inflation condition rather than a revert.

Neither direction includes a decimals-relationship check or a revert path (unlike, say, a bounds check) — the arithmetic just silently produces a wrong, non-reverting result, which is the same root cause identified in the Sense finding (comparing/scaling amounts of differing decimals without validating which side is larger).

### Impact Explanation
- Incoming path: recipients receive a value scaled down by up to 10^(local_decimals) too much — effective loss of virtually all bridged value for any asset pairing where the local asset decimals exceed the paired ERC20 decimals.
- Outgoing path: the amount encoded in the outbound message can be inflated by the same factor relative to what was actually locked/burned, letting a user or attacker cross-chain-mint/release far more value on the destination chain than they escrowed on the source — direct unauthorized fund creation/theft from the custodial pallet account or destination-side liquidity.
- This satisfies the bounty's "stealing or loss of funds" and "transaction manipulation" impact categories, driven purely by asset/decimals configuration rather than any malicious relayer, prover, or admin action — the arithmetic bug fires for any legitimately configured asset pair with this decimal relationship.

### Likelihood Explanation
The bug depends only on how an asset's `Precisions` entry (`erc_decimals`) and the local asset's own decimals are configured — both of which are ordinary governance/admin configuration data, not attacker-controlled input, but nothing in the code prevents or validates this decimal relationship. Any deployment that lists a local asset with higher decimals than its bridged ERC20 twin (a common real-world case — Substrate assets are frequently configured with 18 decimals while many ERC20 stablecoins use 6) triggers the miscalculation on every transfer, with no revert, making this systematically triggerable rather than a rare edge case.

### Recommendation
Remove the `saturating_sub` masking and explicitly branch on the sign of `erc_decimals - local_decimals`, scaling up or down correctly in both directions (mirroring `VWAPOracle::_normalizeAmount`, which already implements the correct bidirectional pattern): [5](#0-4) 

Additionally, validate at `Precisions` write-time (or at `convert_to_balance`/`convert_to_erc20` call sites) that the configured decimals combination is handled by an exact, checked (not saturating) scaling factor, and add regression tests for the `local_decimals > erc_decimals` case in `modules/pallets/testsuite/src/tests/pallet_hyper_fungible_token.rs`.

### Proof of Concept
1. Configure a local asset `X` with 18 decimals; register its `Precisions::<T>::insert(X, evm_contract, 6)` (i.e., the paired ERC20 token — e.g. USDC — has 6 decimals).
2. From the EVM side, send a `PostRequest` with `message.amount = 1000 * 10^6` (1000 USDC-equivalent) to the pallet's `on_accept`.
3. `convert_to_balance` computes divisor `10^(6u8.saturating_sub(18)) = 10^0 = 1`, so `amount = 1000 * 10^6` is minted/transferred directly as the local balance of asset `X` (which has 18 decimals) — i.e., `0.000000000001` tokens instead of the intended `1000` tokens.
4. Conversely, for the outgoing path, a user burning `1000 * 10^18` of local asset `X` will have `convert_to_erc20` compute the same collapsed multiplier and embed `1000 * 10^18` directly as the message amount, which the destination EVM contract will treat as `1000 * 10^18` units of a 6-decimal ERC20 token — i.e., `10^15` times the intended payout, draining the destination-side custody far beyond what was locked.

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

**File:** evm/src/utils/VWAPOracle.sol (L240-248)
```text
    function _normalizeAmount(uint256 amount, uint8 _decimals) private pure returns (uint256 normalized) {
        if (_decimals == 18) {
            return amount;
        } else if (_decimals < 18) {
            return amount * (10 ** (18 - _decimals));
        } else {
            return amount / (10 ** (_decimals - 18));
        }
    }
```
