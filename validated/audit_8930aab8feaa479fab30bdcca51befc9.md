Based on the investigation, the intent-gateway/paymaster/relayer/bandwidth flows in this repo are consistently designed with explicit recovery paths (cancel-from-source/cancel-from-dest refunds, LayerZero endpoint payload retry/clear, WrappedHyperFungibleToken timeout refunds, relayer fee withdrawal, paymaster governance sweep). The one place where I found a self-acknowledged, code-documented analog of the "funds can become permanently stuck with no rescue" bug class is `pallet-ismp`'s `fund_message` extrinsic.

### Title
Permanent Loss of Funds via `fund_message` on Completed ISMP Requests/Responses with No Rescue Path - ([File: modules/pallets/ismp/src/lib.rs])

### Summary
`Pallet::fund_message` lets any signed account top up the relayer-delivery fee for a pending ISMP request or response by commitment hash. The function only checks that a `RequestCommitments`/`ResponseCommitments` entry still exists for the commitment — it does not verify the message is still undelivered/not-timed-out before transferring the caller's tokens to `RELAYER_FEE_ACCOUNT` and bumping `metadata.fee.fee`. The function's own doc comment admits this: "Should not be called on a message that has been completed (delivered or timed-out) as those funds will be lost forever." [1](#0-0)  There is no on-chain view to check delivery/timeout status before calling, and no `rescueFunds`-style recovery function to reclaim the transferred amount if the caller mistakenly (or unknowingly, due to a race with delivery) funds an already-completed message — directly analogous to the reported Pip.sol nullifier-forgetting fund lock.

### Finding Description
`fund_message` performs the transfer unconditionally once metadata is found: [2](#0-1) 
It never checks whether the request/response commitment has already reached a terminal state (delivered or timed out) before crediting `metadata.fee.fee` and moving the caller's balance to `RELAYER_FEE_ACCOUNT`. If the commitment entry is retained in storage for some period after completion (common in this codebase's pattern of async, proof-driven settlement, where receipts/commitments are pruned later rather than removed atomically at delivery), a user who submits `fund_message` concurrently with, or shortly after, delivery/timeout has their tokens moved into `RELAYER_FEE_ACCOUNT` with no relayer ever being incentivized to claim it for that (already-settled) commitment, and no code path returns the excess to the original funder.

### Impact Explanation
This is a genuine unrecoverable fund-loss primitive reachable by any unprivileged, signed account calling a public extrinsic — no malicious relayer, prover, or governance actor is required. Funds sent via `fund_message` on a just-completed message are effectively burned: they sit in `RELAYER_FEE_ACCOUNT` attributed to a commitment no relayer will ever deliver, and there is no `rescueFunds`/refund extrinsic to return them to the original caller.

### Likelihood Explanation
Likelihood is moderate: the race window depends on when a relayer submits delivery/timeout proof relative to when the user submits `fund_message` for the same commitment (e.g., a user trying to bump an underfunded request's fee just as a relayer independently delivers it), and on how long completed commitments remain queryable in storage before being pruned. The pallet authors flagged this exact risk in the docstring rather than guarding against it, indicating it is a known but unmitigated gap.

### Recommendation
Add a completion check before transferring funds in `fund_message` (e.g., verify the request/response has not already been marked delivered/timed out, using the same receipt-state check the delivery/timeout handlers rely on), and/or add a `rescueFunds`-style recovery extrinsic (gated to the original funder) that lets a caller reclaim a mistaken top-up once it can be shown the target commitment was already completed at the time of funding — mirroring the external report's recommendation of an on-chain check plus a recovery function.

### Proof of Concept
1. A user (or relayer) submits an ISMP request/response; `RequestCommitments`/`ResponseCommitments` stores its `FeeMetadata`.
2. Concurrently, a relayer delivers the message (or it times out), settling it, but the commitment entry is not yet pruned from storage.
3. Before pruning, any signed account calls `fund_message` with that commitment and a `message.amount`. The extrinsic only checks `RequestCommitments::<T>::get(commitment).is_some()` [3](#0-2) , which still succeeds, and unconditionally transfers `message.amount` to `RELAYER_FEE_ACCOUNT` [4](#0-3) .
4. Because the message is already completed, no relayer will ever claim this fee bump, and the caller has no path to recover `message.amount`.

**Caveat on verification:** I was unable to fully confirm, from the indexed code alone, the exact block-by-block window during which a completed commitment's `RequestCommitments`/`ResponseCommitments` entry remains queryable before pruning (this determines how easily the race can be triggered in practice). Confirming this would require inspecting the delivery/timeout handlers and pruning logic (e.g., in `modules/ismp/core` and `modules/pallets/ismp/src/host.rs`) directly in a full checkout, since the index did not surface their complete bodies. I recommend a Devin session with full repo access if precise exploitability timing needs to be pinned down before filing.

### Citations

**File:** modules/pallets/ismp/src/lib.rs (L439-480)
```rust
		/// Add more funds to a message (request or response) to be used for delivery and execution.
		///
		/// Should not be called on a message that has been completed (delivered or timed-out) as
		/// those funds will be lost forever.
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().writes(5))]
		#[pallet::call_index(4)]
		pub fn fund_message(
			origin: OriginFor<T>,
			message: FundMessageParams<T::Balance>,
		) -> DispatchResult {
			let account = ensure_signed(origin)?;

			let metadata = match message.commitment {
				MessageCommitment::Request(commitment) => RequestCommitments::<T>::get(commitment),
				MessageCommitment::Response(commitment) =>
					ResponseCommitments::<T>::get(commitment),
			};

			let Some(mut metadata) = metadata else {
				return Err(Error::<T>::MessageNotFound.into());
			};

			T::Currency::transfer(
				&account,
				&RELAYER_FEE_ACCOUNT.into_account_truncating(),
				message.amount,
				Preservation::Expendable,
			)?;

			match message.commitment {
				MessageCommitment::Request(commiment) => {
					metadata.fee.fee += message.amount;
					RequestCommitments::<T>::insert(commiment, metadata);
				},
				MessageCommitment::Response(commiment) => {
					metadata.fee.fee += message.amount;
					ResponseCommitments::<T>::insert(commiment, metadata);
				},
			};

			Ok(())
		}
```
