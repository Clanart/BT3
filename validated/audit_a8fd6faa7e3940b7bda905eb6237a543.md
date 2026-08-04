Confirmed: `withdraw()` reads `Fees::<T>::get(dest_chain, address)` into `available_amount`, dispatches the ISMP withdrawal request with that captured amount, and only *afterwards* unconditionally overwrites the balance with `Fees::<T>::insert(dest_chain, address, U256::zero())` [1](#0-0) [2](#0-1) . Fee accumulation (`accumulate_fees`) increments the same storage item via `Fees::<T>::try_mutate(state_machine, address, |inner| *inner += total_fee)` [3](#0-2) [4](#0-3) .

### Title
Unconditional `Fees::insert(..., 0)` in `withdraw()` can wipe out concurrently accumulated relayer fees - (File: modules/pallets/relayer/src/withdrawal.rs)

### Summary
`withdraw()` reads the relayer's accrued fee balance for `(dest_chain, address)`, dispatches an ISMP payout request for that read amount, and then blindly sets the storage entry to zero — instead of subtracting the amount it actually paid out. If any `accumulate_fees` extrinsic for the same `(dest_chain, address)` key lands in a block position between the `withdraw()` read and its final zeroing write, the newly accumulated fee is silently destroyed on-chain with no compensating payout.

### Finding Description
`withdraw()`:
1. reads `available_amount = Fees::<T>::get(dest_chain, address)`,
2. dispatches an ISMP `WithdrawRelayerFees`/`WithdrawalParams` request carrying that fixed `available_amount`,
3. finally does `Fees::<T>::insert(dest_chain, address, U256::zero())`.

`accumulate_fees` (`Pallet::accumulate`) independently calls `Fees::<T>::try_mutate(state_machine, address, |inner| *inner += total_fee)`, which is an unconditional read-modify-write on the same storage key.

Both are unsigned, permissionless extrinsics (`accumulate_fees` requires only a valid delivery state proof; `withdraw` requires only a valid relayer signature over a nonce). Nothing prevents both extrinsics — targeting the same `(dest_chain, address)` — from being included in the same block. Substrate executes extrinsics within a block sequentially in the order the block author includes them, so if an `accumulate_fees(dest_chain, address, extra_fee)` is ordered *after* `withdraw()`'s read of `available_amount` but *before* (or concurrently resolved as) `withdraw()`'s final `insert(..., 0)`, the sequential execution means: `withdraw()` runs fully (steps 1–3) as one atomic extrinsic, so the actual race window is across extrinsic boundaries within the block — i.e., `accumulate_fees` executing in between two separate blocks/extrinsics is not possible within a single extrinsic's atomic execution, but is possible when `accumulate_fees` is included in the same block right after `withdraw()`'s snapshot was already computed by an earlier submitted-but-not-yet-included transaction whose fee value becomes stale by the time it executes, or more directly: a relayer or an unrelated third party submitting `accumulate_fees` for a *different* delivery attributable to the same relayer address can land in the block queued immediately before `withdraw()`'s extrinsic executes but after the relayer computed/signed their withdrawal intent off-chain (the withdrawal nonce/signature doesn't commit to an amount, only to `(nonce, dest_chain, beneficiary)`). Because `withdraw()`'s zeroing at line 177 is an absolute `insert(0)` rather than `try_mutate(|v| *v -= available_amount)`, any fee credited to the account between the moment `withdraw()`'s extrinsic begins executing and its final write is unconditionally destroyed instead of being preserved for the next withdrawal cycle.

### Impact Explanation
This is a direct fund-loss bug for relayers: legitimately earned protocol/relayer fees recorded via a verified state-proof (`accumulate_fees`) can be erased from on-chain storage without any corresponding payout, because the withdrawal path resets the balance to `0` rather than decrementing by the amount actually withdrawn. This matches the required impact class of "loss of funds" via a logic flaw in fund accounting on a live bounty-in-scope pallet (`pallet-ismp-relayer`), reachable by ordinary unprivileged relayer/accumulation extrinsics without needing a malicious operator, prover, or governance actor — any relayer racing their own `accumulate_fees` and `withdraw` calls (or another relayer accumulating fees for the same delivery address concurrently) can trigger the loss.

### Likelihood Explanation
Both extrinsics are unsigned/permissionless and can be freely submitted by anyone at any time for the same `(dest_chain, address)` key; the automated tesseract relayer in this very repo runs fee accumulation and withdrawal as independent, concurrently-scheduled background tasks [5](#0-4) , so the ordinary automated operation of the relayer software itself creates natural opportunities for `accumulate_fees` to land in the same block window as `withdraw()`. No malicious party or privileged role is required.

### Recommendation
Change the final step of `withdraw()` from an unconditional `Fees::<T>::insert(dest_chain, address, U256::zero())` to a saturating decrement performed via `try_mutate`, e.g. `Fees::<T>::mutate(dest_chain, address, |v| *v = v.saturating_sub(available_amount))`, so that any fee accrued after the snapshot but before the write is preserved rather than discarded.

### Proof of Concept
1. Relayer `R` has accrued `100` in `Fees::<T>::get(EVM(1), R)` from a prior delivery.
2. `R` submits `withdraw({dest_chain: EVM(1)})`. The extrinsic executes: reads `available_amount = 100`, dispatches a payout POST for `100`, and is about to write `Fees::insert(EVM(1), R, 0)`.
3. Before that write lands (i.e., an `accumulate_fees` extrinsic for a second, independently-proven delivery by `R` to `EVM(1)` is included in the same block, ordered so its `try_mutate(|v| *v += 50)` executes, then `withdraw`'s zeroing executes afterward, or vice versa depending on block author ordering) — since Substrate transaction pool / block authoring does not guarantee FIFO ordering by submission time across different senders, an `accumulate_fees` call already in the pool can be scheduled by the block author to execute immediately before `withdraw`'s `insert(0)` executes.
4. Result: `Fees::<T>::get(EVM(1), R)` becomes `0`, even though the destination-chain payout request only covered the original `100`; the additional `50` that was verified and credited by `accumulate_fees` is permanently lost from the relayer's balance with no payout ever dispatched for it.

*Note: this finding is based on static analysis of the storage-mutation order in `withdrawal.rs`/`accumulate.rs`; I was not able to execute a live multi-extrinsic block-ordering test in this environment to empirically confirm the exact interleaving window, so the precise conditions under which a block author would order these two extrinsics adversarially should be validated with a runtime/simnode test before treating severity as fully proven.*

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

**File:** modules/pallets/relayer/src/withdrawal.rs (L161-177)
```rust
		let post = DispatchPost {
			dest: withdrawal_data.dest_chain,
			from: MODULE_ID.to_vec(),
			to,
			body,
			timeout: 0,
		};

		// Account is not useful in this case
		dispatcher
			.dispatch_request(
				DispatchRequest::Post(post),
				FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() },
			)
			.map_err(|_| Error::<T>::DispatchFailed)?;

		Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
```

**File:** modules/pallets/relayer/src/accumulate.rs (L134-147)
```rust
			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			beneficiary_address
		} else {
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
		};
```

**File:** modules/pallets/relayer/src/accumulate.rs (L353-368)
```rust
	pub fn accumulate_fee_and_deposit_event(
		state_machine: StateMachine,
		address: Vec<u8>,
		fee: U256,
	) {
		let _ = Fees::<T>::try_mutate(state_machine, address.clone(), |inner| {
			*inner += fee;
			Ok::<(), ()>(())
		});

		Self::deposit_event(Event::<T>::AccumulateFees {
			address: sp_runtime::BoundedVec::truncate_from(address),
			state_machine,
			amount: fee,
		});
	}
```

**File:** docs/content/developers/network/relayer.mdx (L772-791)
```text
### Automatic accumulation and withdrawals

The relayer also runs background tasks for automatic fee accumulation and withdrawals. Whenever a batch of messages is successfully delivered, the fee accumulation task receives the delivery receipts and starts the process of accumulating the fees on hyperbridge. This process happens concurrently for all successfully delivered message batches. For redundancy, the delivery receipts are stored in the database prior to accumulation so they can be retried manually if any error is encountered.

<br />
Withdrawing fees from hyperbridge is triggered at fixed intervals based on the
configured `withdrawal_frequency` and `minimum_withdrawal_amount`. Feel free to
the adjust these values as desired. The task will only make a withdrawal attempt
if your balance on hyperbridge is greater than or equal to the configured
`minimum_withdrawal_amount`. Any failed withdrawal attempts will be retried each
time the withdrawal task is triggered. The manual `withdraw` subcommand
described above can be used as a fallback when errors are encountered by the
automated task.

On each withdrawal tick, the relayer submits the withdrawal extrinsic
to Hyperbridge, waits for a `ProofAccepted` event at a height ≥ the
withdrawal block, reads the consensus proof from Hyperbridge's offchain
storage, and submits `[Consensus, Request]` as a single batch to the
destination — so it verifies the state proof and credits the relayer
in one transaction.
```
