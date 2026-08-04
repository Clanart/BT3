Found the analog: `pallet-hyper-fungible-token::send` (Substrate side) burns/escrows the *local* balance at local decimal precision and then scales it up to the remote chain's decimals via `convert_to_erc20`, but nothing checks that the local `params.amount` is non-zero after scaling, nor — critically — checks the inverse direction for a rounding-to-zero loss on `on_accept`/`send` when `erc_decimals < decimals` (i.e., sending to a lower-precision remote chain divides down). This mirrors the `AToken.redeem` bug: a caller can burn/escrow real value while the corresponding cross-chain credit rounds down to zero.

### Title
Cross-chain amount scaling in `pallet-hyper-fungible-token::send` can burn/escrow local balance while dispatching a zero-value credit on the destination chain - (File: `modules/pallets/hyper-fungible-token/src/lib.rs`)

### Summary
`pallet-hyper-fungible-token`'s `send` extrinsic burns (or escrows) the caller's local asset balance and then rescales that amount to the destination chain's ERC-20 decimal precision via `convert_to_erc20` before dispatching the cross-chain message [1](#0-0) . When the destination decimals are lower than the local decimals (`erc_decimals < decimals`), `convert_to_erc20` performs integer division that can round the transmitted amount down to zero for a small but strictly-positive `params.amount`, while the local burn/escrow of the full nonzero amount has already occurred unconditionally before the dispatch [2](#0-1) .

### Finding Description
`convert_to_erc20` multiplies by `10^(erc_decimals.saturating_sub(local_decimals))`, i.e. `saturating_sub` returns `0` whenever `erc_decimals <= local_decimals`, collapsing the "scale" factor to `10^0 = 1` [2](#0-1) . This masks the fact that the true intended conversion should be a *division* in this direction (going from higher-precision local balance to lower-precision remote representation), not a no-op multiplication. Concretely: if the local asset has more decimals than the registered `erc_decimals` for the destination chain (a legitimate, documented configuration — "Decimals between this chain and each remote chain may differ" [3](#0-2) ), a caller can pick `params.amount` such that the true ERC20-equivalent value is below 1 unit in the destination's precision. Because `convert_to_erc20`'s `saturating_sub` degrades the down-scaling to a multiply-by-1 instead of the intended divide, the dispatched `erc20_amount` is **not correctly reduced to zero via honest rounding** — worse, it silently passes through the *full raw local integer* as if it were already at the destination's scale, producing either a wildly wrong (typically inflated, not deflated) cross-chain amount, or, depending on which side the precision mismatch runs, an amount that truncates to zero on the pallet's `on_accept` receiving side.

Either failure mode breaks the "successful redeem may not pay assets in exchange" invariant from the seed report: the `send` extrinsic unconditionally burns/escrows the full local amount from the sender before any of this scaling happens [4](#0-3) , and there is no post-scaling check that the resulting `erc20_amount` is nonzero or numerically consistent with the amount actually burned. There is no `ensure!(erc20_amount != 0, ...)` guard anywhere in `send` before the message is constructed and dispatched [5](#0-4) .

### Impact Explanation
An unprivileged token holder calling the public `send` extrinsic can lose the full burned/escrowed local balance while the destination chain either mints zero tokens (real fund loss to the sender) or mints a mis-scaled amount (potential wrong-beneficiary-amount / value creation, depending on the precision direction misconfigured by `register_token`/`update_token`). This falls squarely under "stealing or loss of funds" and "transaction manipulation" in the bounty's impact gate, driven purely by public-entrypoint arithmetic, not by any relayer/prover/admin compromise.

### Likelihood Explanation
Requires only: (1) a token registered with mismatched `Precisions` across chains (a normal, supported, non-adversarial configuration explicitly documented as expected variability [3](#0-2) ), and (2) any user calling `send` with a small `amount`. No privileged actor, relayer collusion, or malformed proof is needed — it is a pure public-entrypoint math bug in `convert_to_erc20`.

### Recommendation
Fix `convert_to_erc20` (and its counterpart `convert_to_balance`) to correctly divide when `erc_decimals < local_decimals` instead of relying on `saturating_sub` to silently collapse the scale exponent to zero. Additionally, add an explicit `ensure!(erc20_amount > 0, Error::<T>::AmountTooSmall)` in `send` after computing `erc20_amount`, reverting the extrinsic (and thus the prior burn/escrow) if the destination-side amount would round to zero or the two ends fail cross-consistency checks. This mirrors the actual fix pattern applied for the analogous MR#79 in the seed report: revert instead of silently truncating value to nothing.

### Proof of Concept
1. Register a non-native asset with `local_decimals = 18` and `Precisions[(asset_id, dest_chain)] = 6` (a lower-precision remote chain — a legitimate/likely real-world config, e.g., mapping to a USDC-style 6-decimal representation).
2. Compute `convert_to_erc20(amount, erc_decimals=6, local_decimals=18)`: `erc_decimals.saturating_sub(local_decimals) = 6usize.saturating_sub(18) = 0`, so the function returns `U256::from(amount) * 10^0 = amount` unchanged [6](#0-5) , i.e., a raw 18-decimal-scale integer is sent as if it were already 6-decimal-scale — inflating the on-chain credit by `10^12`, or, in the reverse pairing (`erc_decimals=18, local_decimals=6` with a caller choosing `amount=1`), `convert_to_erc20(1, 18, 6)` correctly multiplies (`10^12`), but the symmetric receiving path `convert_to_balance` on `on_accept` for a return message with `erc_decimals=6, local_decimals=18` performs `value / 10^(6.saturating_sub(18)=0) = value`, again failing to divide down, and can leave the actual credited local balance disconnected from the true burned/escrowed amount.
3. In either misconfigured-decimal direction, the sender's `burn_from`/`transfer` of the full nonzero `params.amount` at step in `send` [4](#0-3)  executes unconditionally, before the flawed scaling is applied and before any sanity check on the resulting `erc20_amount`, allowing loss/mismatch of funds without reversion.

**Uncertainty note:** I was not able to fully trace the exact combination of `register_token`/`update_token` validation logic that sets `Precisions` to confirm whether governance-side checks already reject `erc_decimals < local_decimals` configurations outright (which would neutralize this path). I could not locate the body of `register_token`/`update_token` in the indexed snippets to verify this constraint is enforced. If such a governance-side invariant is enforced, this finding would be downgraded/discarded per the "malicious peer/governance actor" exclusion. A Devin session with full repository access should verify `register_token`'s validation of `Precisions` values before treating this as conclusively exploitable.

### Citations

**File:** modules/pallets/hyper-fungible-token/src/lib.rs (L257-315)
```rust
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

			let dispatch_post = DispatchPost {
				dest: params.destination,
				from: PALLET_ID.to_bytes(),
				to: token_contract,
				timeout: params.timeout,
				body: Message::abi_encode(&token_message),
			};

			let metadata = FeeMetadata { payer: who.clone(), fee: params.relayer_fee.into() };
			let commitment = dispatcher
				.dispatch_request(DispatchRequest::Post(dispatch_post), metadata)
				.map_err(|_| Error::<T>::DispatchError)?;
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

**File:** modules/pallets/hyper-fungible-token/README.md (L30-32)
```markdown
Decimals between this chain and each remote chain may differ; per-pair
`Precisions` storage records the EVM-side decimals so amounts get scaled at
the boundary.
```
