Found a concrete, provable analog in `pallet-hyper-fungible-token`'s decimal-scaling helpers.

### Title
Asymmetric `saturating_sub` in `convert_to_balance`/`convert_to_erc20` causes silent amount truncation when local decimals exceed remote decimals - (File: modules/pallets/hyper-fungible-token/src/impls.rs)

### Summary
The bug report's core invariant is: a `max(x, 0)` clamp that should represent "floor at a safe minimum" instead silently zeroes out a value whenever the input goes negative, letting an attacker exploit the clamped-to-zero state. The local analog is the pallet's use of `erc_decimals.saturating_sub(local_decimals)` to compute the scaling exponent for cross-chain token amount conversion. Like the pool fee bug, a subtraction that can legitimately go negative is clamped to `0` instead of being handled bidirectionally, silently collapsing the scaling factor to `10^0 = 1` and corrupting the bridged amount.

### Finding Description
`convert_to_balance` and `convert_to_erc20` both compute the decimal-scaling exponent as `erc_decimals.saturating_sub(local_decimals)`: [1](#0-0) 

This is only correct when `erc_decimals >= local_decimals`. If a registered asset has `local_decimals > erc_decimals` (e.g., a Substrate asset configured with 18 decimals bridging to an EVM contract token with 6 decimals), `erc_decimals.saturating_sub(local_decimals)` clamps to `0` instead of the true (negative) exponent. The scaling factor becomes `10^0 = 1` instead of the correct downscaling factor, so the amount is used unscaled.

This helper is used on both the outbound path (`send`) and the two inbound paths (`on_accept` and `on_timeout`): [2](#0-1) [3](#0-2) [4](#0-3) 

Decimals are governance-configured per `(asset, chain)` via `Precisions` (set by `register_token`/`update_token`), so this is not directly attacker-controlled — but it is a silent, un-guarded arithmetic clamp with no validation (`register_token` does not enforce `erc_decimals >= local_decimals`, unlike `set_tier` in the bandwidth pallet which does validate its inputs). Any misconfiguration where the local asset has more decimals than the registered EVM-side decimals causes amounts to be under- or over-scaled by orders of magnitude on every mint/burn/escrow-release, exactly mirroring how `pool::get_fee`'s clamp silently produced a wrong (zero) result instead of erroring or computing correctly in both directions.

### Impact Explanation
If `local_decimals > erc_decimals` for a registered pair, `send()` computes `erc20_amount` using scale factor `1` instead of downscaling — inflating the amount encoded to the remote chain by `10^(local_decimals - erc_decimals)`, so a user burning/escrowing a small local amount causes the remote `HyperFungibleToken.onAccept` to mint a vastly larger amount to the beneficiary. Symmetrically, on `on_accept`/`on_timeout`, an inbound message amount gets divided by `1` instead of the correct larger divisor, over-crediting the beneficiary locally relative to what was actually escrowed/burned on the source side. This directly causes wrong-amount minting versus escrow, i.e., fund loss/inflation across the bridge boundary — a beneficiary receives an incorrect amount not backed by matching custody.

### Likelihood Explanation
This requires no relayer/attacker collusion, no proof forgery — it is purely a governance-configuration-dependent latent arithmetic bug in production code, triggered by ordinary `register_token`/`update_token` calls that don't validate the decimals ordering, combined with normal user `send()` calls or delivered messages. Given how straightforward it is to configure an asset with `local_decimals > erc_decimals` (e.g., an 18-decimal native/Substrate asset bridging to a 6-decimal EVM token), likelihood of the misconfiguration existing in a real deployment is plausible but requires that specific config; I cannot verify from the index whether `register_token`/`update_token` are validated elsewhere to forbid `erc_decimals < local_decimals`.

### Recommendation
Replace the asymmetric `saturating_sub` scaling with an explicit bidirectional scale that multiplies or divides depending on the sign of `erc_decimals - local_decimals`, and add a validation check in `register_token`/`update_token` rejecting decimal configurations that would cause this ambiguity to go unnoticed, or reject registration entirely and require explicit up/down scaling factors instead of relying on unchecked subtraction.

### Proof of Concept
1. Governance registers a non-native asset with `local_decimals = 18` (e.g., via the pallet's fungible `Assets` backend) and sets `Precisions::<T>::insert(asset_id, dest_chain, 6)` (erc_decimals = 6) for the corresponding EVM contract.
2. A user calls `send(params)` with `amount = 1_000_000_000_000_000_000` (1 token at 18 decimals).
3. Inside `send`, `convert_to_erc20(amount, erc_decimals=6, local_decimals=18)` computes `10u128.pow(6u8.saturating_sub(18u8) as u32)` = `10u128.pow(0)` = `1`, instead of the correct division by `10^12`.
4. The dispatched `Message.amount` therefore equals `1_000_000_000_000_000_000` in ERC20 units instead of the intended `1_000_000` (6-decimal) units — a 10^12x inflation is minted on the destination `HyperFungibleToken.onAccept`, while only the correct 1-token amount was burned/escrowed on the source chain. [5](#0-4)

### Citations

**File:** modules/pallets/hyper-fungible-token/src/impls.rs (L43-59)
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
}
```

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
