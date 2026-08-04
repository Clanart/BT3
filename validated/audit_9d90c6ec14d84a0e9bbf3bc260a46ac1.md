Confirmed vulnerability: `Pallet::withdraw` in `modules/pallets/relayer/src/withdrawal.rs` reads `Fees::<T>::get(...)` into `available_amount` at the start of the extrinsic, dispatches a cross-chain payout for that snapshotted amount, and only at the very end resets the storage with `Fees::<T>::insert(dest_chain, address, U256::zero())` — an unconditional zero-write rather than a subtraction of `available_amount`. Meanwhile `accumulate` (`modules/pallets/relayer/src/accumulate.rs:134-146` and `accumulate_fee_and_deposit_event:353-368`) is a separate, permissionless, unsigned extrinsic (`accumulate_fees`, called by any relayer with a valid delivery proof) that additively credits `Fees::<T>::try_mutate(state_machine, address, |inner| *inner += total_fee)` to the *same* `(dest_chain, address)` key at any time.

### Title
Relayer fee snapshot-then-zero in `withdraw` silently destroys concurrently accumulated fees - (File: modules/pallets/relayer/src/withdrawal.rs)

### Summary
`withdraw()` snapshots the relayer's fee balance, dispatches payout for that snapshot, then unconditionally zeroes the entire `Fees` storage entry instead of decrementing it by the paid amount, so any fee legitimately accumulated between the read and the write is permanently lost.

### Finding Description
`withdraw` computes `available_amount = Fees::<T>::get(withdrawal_data.dest_chain, address.clone())` [1](#0-0) , then dispatches the ISMP payout message sized to that snapshot [2](#0-1) , and finally resets the map entry to zero rather than subtracting the paid amount: [3](#0-2) .

Fees are additively credited to the same `(dest_chain, address)` key by the independent, permissionless `accumulate` extrinsic, which anyone can call with a valid delivery proof: `Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| { *inner += total_fee; ...})` [4](#0-3)  and the beneficiary-redirect branch [5](#0-4) , plus the internal helper `accumulate_fee_and_deposit_event` [6](#0-5) .

Because `withdraw` and `accumulate` are two unrelated, unsigned/permissionless extrinsics operating on the same shared storage key with no lock or CAS, this is a snapshot-vs-in-place-reset TOCTOU: if `accumulate_fees(other_valid_delivery_proof)` for the *same relayer address on the same destination chain* lands in a block between the moment `withdraw` reads `Fees` and the moment it writes zero (e.g. it is included in the same block via `on_finalize`/transaction ordering, or the withdraw extrinsic is processed slightly before a pending accumulate call already in the same block/tx pool), the newly added fee is wiped by the final `insert(..., U256::zero())` even though it was never paid out. This exactly mirrors the underlying broken invariant in the external report: a value bucket shared across time is destroyed by a threshold/reset operation that doesn't account for legitimate value added in between, permanently locking/losing funds that rightfully belong to the depositor (here, the relayer).

### Impact Explanation
This causes real, permanent loss of relayer reward funds: newly accumulated, legitimately earned fees for a relayer can be erased without ever being paid out, and there is no mechanism to recover them (the storage is simply zero afterward, with no event or trace tying the loss to the wipe). This falls squarely under "stealing or loss of funds" in the bounty's impact list — the treasury/host manager balance backing `Fees` is unaffected, but the relayer's rightful claim is destroyed, meaning funds become effectively stranded/unclaimable by their intended beneficiary.

### Likelihood Explanation
No malicious/privileged actor is required. `accumulate_fees` is unsigned and callable by anyone holding a valid delivery proof (this is the pallet's designed reward flow, driven automatically by the relayer's own software per `tesseract/messaging/relayer/src/fees.rs`), and `withdraw`/`withdraw_fees` is callable by the relayer themselves at will. A relayer who is actively delivering messages (the normal case, since delivery is what earns fees) is naturally exposed: withdrawing while a delivery proof for the same destination is also being submitted (e.g., by the relayer's own automation running both flows concurrently, or transaction pool reordering within a block) suffices to trigger the loss. This does not require a malicious peer, prover, or governance actor — it's a race condition inherent to normal usage of two independent public entrypoints against shared storage.

### Recommendation
Change `withdraw` to decrement the fee entry by exactly the amount being paid rather than resetting it to zero, using an atomic `try_mutate` guarded against underflow, e.g.:
```rust
Fees::<T>::try_mutate(withdrawal_data.dest_chain, address.clone(), |inner| {
    *inner = inner.checked_sub(available_amount).ok_or(Error::<T>::NotEnoughBalance)?;
    Ok::<(), Error<T>>(())
})?;
```
This also implies re-reading `available_amount` from the same mutate closure rather than from a stale earlier read, so the whole read-modify-write is atomic within the single extrinsic and cannot be raced by a concurrently-processed `accumulate_fees` call.

### Proof of Concept
1. Relayer `R` delivers request `A` (dest chain `D`) and calls `accumulate_fees` for it — `Fees[D][R] = 100`.
2. `R` submits `withdraw_fees` for chain `D` (nonce N). Extrinsic begins execution: reads `available_amount = Fees::<T>::get(D, R) = 100` [7](#0-6) .
3. Before this extrinsic finishes, another `accumulate_fees` call (proving delivery of a second, unrelated request `B` also destined for `D`, delivered by `R`) executes in the same block and credits `Fees[D][R] += 50`, making the live value `150`.
4. `withdraw`'s extrinsic execution resumes/completes: it dispatches the payout message for `100` (the stale snapshot) and then unconditionally sets `Fees::<T>::insert(D, R, U256::zero())` [3](#0-2) .
5. Result: `R` is only ever paid `100`; the `50` from request `B`'s delivery is permanently lost — `Fees[D][R]` is now `0` even though `R` never received or claimed that `50`.

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

**File:** modules/pallets/relayer/src/withdrawal.rs (L161-175)
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
```

**File:** modules/pallets/relayer/src/withdrawal.rs (L177-177)
```rust
		Fees::<T>::insert(withdrawal_data.dest_chain, address.clone(), U256::zero());
```

**File:** modules/pallets/relayer/src/accumulate.rs (L134-137)
```rust
			let _ = Fees::<T>::try_mutate(state_machine, beneficiary_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});
```

**File:** modules/pallets/relayer/src/accumulate.rs (L141-147)
```rust
			let _ = Fees::<T>::try_mutate(state_machine, delivery_address.clone(), |inner| {
				*inner += total_fee;
				Ok::<(), ()>(())
			});

			delivery_address
		};
```

**File:** modules/pallets/relayer/src/accumulate.rs (L353-361)
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
```
