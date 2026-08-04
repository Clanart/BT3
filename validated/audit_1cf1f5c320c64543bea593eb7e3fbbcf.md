Found a concrete analog. The broken invariant from the Stader report — a claimed amount is silently truncated/capped to something smaller than the actual owed value while the ledger is zeroed as if the debt were paid in full — reproduces in `pallet-relayer`'s substrate withdrawal path.

### Title
Relayer fee withdrawal to substrate destinations truncates `U256` fees via `low_u128()`, permanently burning any accumulated balance above `u128::MAX` while zeroing the full ledger entry - (File: `modules/pallets/relayer/src/withdrawal.rs`)

### Summary
`Pallet::withdraw` reads the relayer's accumulated fee balance as a `U256` from the `Fees` storage map, but when dispatching the payout message to a substrate destination it narrows that value with `available_amount.low_u128()` [1](#0-0) , while the full-precision `U256` is what gets zeroed out of storage immediately after dispatch [2](#0-1) .

### Finding Description
`Fees` is a `StorageDoubleMap<..., U256, ValueQuery>` [3](#0-2) , accumulated over time by `accumulate_fees`, which sums proven per-request fees with `*entry += fee` with no upper bound check [4](#0-3) . When the relayer calls `withdraw_fees`, `available_amount` is fetched in full `U256` precision [5](#0-4) . For a substrate destination, the payout body embeds `amount: available_amount.low_u128()` — this silently drops the high 128 bits of the balance instead of erroring or saturating [6](#0-5) . Immediately afterward, regardless of which branch executed, the code sets `Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero())` [7](#0-6) , marking the *entire* pre-truncation balance as settled. This exactly mirrors the Stader bug class: the code computes the correct "owed" quantity (`available_amount`, analogous to `penaltyAmount`), substitutes a smaller/clamped quantity when constructing the actual settlement action (`low_u128()`, analogous to `min(sdToSlash, minThreshold)`), and then marks the full original debt as resolved (`Fees` zeroed / `penaltyAmount = operatorShare`) — permanently losing the difference with no recovery path. The EVM branch does not have this specific truncation (it ABI-encodes the full `U256` via `.into()` at line 151), so the bug is isolated to substrate destinations.

### Impact Explanation
Any relayer whose accumulated, *legitimately earned* fee balance for a given `(dest_chain, address)` pair exceeds `u128::MAX` in the fee token's smallest unit has the excess permanently and silently destroyed: the withdrawal message dispatched to the destination only instructs payout of the truncated `low_u128()` amount, yet the source-of-truth ledger is reset to zero as though the full `U256` amount had been paid. There is no re-withdrawal path for the lost remainder because the `Fees` entry no longer reflects it. This is unauthorized loss of relayer-earned funds enforced by protocol logic itself (not a peer/relayer/admin compromise) — it fires purely from normal fee accumulation and the standard `withdraw_fees` extrinsic once cumulative fees cross the u128 boundary.

### Likelihood Explanation
`u128::MAX` (~3.4×10^38) is an enormous bound relative to any realistic single fee-token balance in base units, so under today's economic parameters this requires an extreme accumulation (e.g., years of very high-fee traffic on a low-decimal-count token, or a fee-token re-pricing) before triggering. It is not attacker-triggerable in a single transaction, but it is a deterministic, reachable, non-privileged code path — any relayer that naturally accumulates enough fees hits irreversible fund loss with no malicious actor required, satisfying the "loss of funds" impact criterion even though the trigger threshold is high.

### Recommendation
Reject (or saturate with an explicit error) when `available_amount > u128::MAX::into()` before constructing the substrate `WithdrawalRequest`, e.g. via `available_amount.try_into().map_err(|_| Error::<T>::AmountOverflow)?` instead of `.low_u128()`. Only zero the `Fees` entry for the amount actually included in the dispatched payout, or reject the whole withdrawal until the balance is brought under the representable range (e.g., by supporting chunked withdrawals).

### Proof of Concept
1. Via `accumulate_fees`, accrue proven fees into `Fees::<T>::get(dest_chain, relayer)` until the `U256` value exceeds `u128::MAX` (e.g., through repeated `Self::validate_results` accumulation with large `fee` values pulled from a manipulated-decimals or simply long-running high-volume fee token, as summed unconditionally at `*entry += fee` [8](#0-7) ).
2. Call `withdraw_fees` with `dest_chain` set to any substrate `StateMachine` (`is_substrate() == true`).
3. Inside `Pallet::withdraw`, `available_amount` holds the full `U256`, but the dispatched `WithdrawalRequest.amount` is `available_amount.low_u128()` — the high bits are dropped [6](#0-5) .
4. `Fees::<T>::insert(dest_chain, relayer, U256::zero())` runs unconditionally right after dispatch [7](#0-6) , so the ledger shows the relayer as fully paid.
5. The destination-chain `HYPERBRIDGE_MODULE_ID` handler only ever receives and pays out the truncated `low_u128()` amount; the difference between the real balance and `u128::MAX`-wrapped value is unrecoverable — no code path re-credits it, matching the "remaining penalty" that Stader's `settleFunds()`/`slashValidatorSD()` never recovered.

### Citations

**File:** modules/pallets/relayer/src/withdrawal.rs (L116-123)
```rust
		let available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone());

		if available_amount <
			Self::min_withdrawal_amount(withdrawal_data.dest_chain)
				.unwrap_or(MinWithdrawal::get())
		{
			Err(Error::<T>::NotEnoughBalance)?
		}
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L133-143)
```rust
		let beneficiary_address = withdrawal_data.beneficiary.clone().unwrap_or(address.clone());
		let (to, body) = match withdrawal_data.dest_chain {
			s if s.is_substrate() => (
				HYPERBRIDGE_MODULE_ID.to_vec(),
				Message::WithdrawRelayerFees(WithdrawalRequest {
					amount: available_amount.low_u128(),
					account: AccountId32::try_from(&beneficiary_address[..])
						.map_err(|_| Error::<T>::InvalidPublicKey)?,
				})
				.encode(),
			),
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L170-177)
```rust
		dispatcher
			.dispatch_request(
				DispatchRequest::Post(post),
				FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() },
			)
			.map_err(|_| Error::<T>::DispatchFailed)?;

		Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
```

**File:** modules/pallets/relayer/src/lib.rs (L111-122)
```rust
	/// double map of address to source chain, which holds the amount of the relayer address
	#[pallet::storage]
	#[pallet::getter(fn relayer_fees)]
	pub type Fees<T: Config> = StorageDoubleMap<
		_,
		Blake2_128Concat,
		StateMachine,
		Blake2_128Concat,
		Vec<u8>,
		U256,
		ValueQuery,
	>;
```

**File:** modules/pallets/relayer/src/accumulate.rs (L293-299)
```rust
				proof.dest_proof.height.id.state_id,
				&encoded_receipt,
			)?;
			let entry = result.entry(address).or_insert(U256::zero());
			*entry += fee;
			commitments.push(commitment);
		}
```
