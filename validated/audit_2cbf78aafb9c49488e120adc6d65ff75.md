## Finding: `fund_message` Lets Users Lock Funds in `RELAYER_FEE_ACCOUNT` With No Recovery Path

### Title
Unchecked `fund_message` Call on an Already-Delivered/Claimed Request Permanently Locks Funds — ([File: modules/pallets/ismp/src/lib.rs])

### Summary
`pallet-ismp`'s `fund_message` extrinsic transfers caller funds into `RELAYER_FEE_ACCOUNT` and bumps the `fee` field of a request/response's stored `RequestMetadata`/`FeeMetadata`, but it never checks whether the underlying commitment has already been delivered/timed-out and its relayer fee already claimed. Because the `claimed` flag on `RequestCommitments` is never reset by `fund_message`, any top-up added after the fact is added to a fee pool that no subsequent `accumulate` call can ever pay out — the funds move into the shared `RELAYER_FEE_ACCOUNT` custody and become permanently inaccessible, mirroring the audited AMM bug where locked tokens accumulate in a pool with no accounted outflow.

### Finding Description
`fund_message` looks up the commitment's metadata, transfers `message.amount` from the caller into `RELAYER_FEE_ACCOUNT`, and increments `metadata.fee.fee`: [1](#0-0) 

The function's own doc comment concedes the danger but does not enforce it in code: *"Should not be called on a message that has been completed (delivered or timed-out) as those funds will be lost forever."* — an acknowledged-but-unmitigated footgun, exactly matching the original report's "Acknowledged, not fixed" status for the AMM locked-funds bug.

The relayer fee accumulation path (`pallet-ismp-relayer::accumulate`) reads this exact metadata and only pays out fees for commitments whose `RequestCommitments` entry is `!leaf_meta.claimed`: [2](#0-1) 

Once a request is delivered, its commitment entry is marked `claimed = true` (it is never deleted from storage, only flagged) — the same accumulate module uses this flag as the sole gate on whether stored fee metadata is still payable: [3](#0-2) 

`fund_message` performs no check on this `claimed` flag before transferring the caller's tokens into `RELAYER_FEE_ACCOUNT`: [4](#0-3) 

This is structurally identical to the AMM bug: tokens are moved into a shared custodial pool (`RELAYER_FEE_ACCOUNT`, analogous to `lockedFunds`) from a code path (`fund_message`) whose outflow is only ever accounted for by a *different*, narrower mechanism (`accumulate`'s `!claimed` filter). Once the `claimed` gate has already flipped `true`, that outflow path can never again select the commitment, so the newly added balance sits in `RELAYER_FEE_ACCOUNT` with no code path that references it — permanently inaccessible, just like the excluded "locked funds" in the original report's total-assets calculation.

### Impact Explanation
Any unprivileged, signed account can call `fund_message` at any time on any request/response commitment, including ones that have already been delivered and had their fee fully claimed by a relayer. Doing so (accidentally, via a stale UI, or a race with a delivering relayer) irrecoverably transfers the caller's tokens into `RELAYER_FEE_ACCOUNT` — funds are lost with no recovery mechanism, satisfying the "loss of funds" bounty category.

### Likelihood Explanation
This requires no privileged actor, relayer collusion, or malformed proof — only a normal user racing (or being late relative to) request delivery, which is a routine, permissionless timing condition in ISMP's async request lifecycle. The self-acknowledging doc comment indicates the maintainers are aware the hazard exists but have not added an on-chain guard, making accidental or exploitable triggering realistic in production usage (e.g., automated fee-bumping bots that don't first check delivery/timeout status).

### Recommendation
Before transferring funds and mutating metadata in `fund_message`, check the commitment's `claimed` status (and any timeout status) on `RequestCommitments`/`ResponseCommitments` and reject the call with an error (e.g., `Error::<T>::MessageAlreadyCompleted`) if the message has already been delivered, responded to, or timed out — turning the current doc-only warning into an enforced invariant.

### Proof of Concept
1. User A dispatches a POST request via `IsmpDispatcher::dispatch_request` with a small relayer fee, producing `commitment`.
2. A relayer delivers the request to the destination and, via `pallet-ismp-relayer::accumulate`, submits proofs of delivery; `RequestCommitments::<T>::get(commitment).claimed` flips to `true` and the relayer withdraws its fee.
3. User B (or User A again, unaware of delivery) calls `Ismp::fund_message(origin, FundMessageParams { commitment: MessageCommitment::Request(commitment), amount })`.
4. `fund_message` succeeds: it finds `Some(metadata)` in storage (the entry is never removed, only flagged), transfers `amount` from the caller to `RELAYER_FEE_ACCOUNT`, and increments `metadata.fee.fee`, per [4](#0-3) .
5. Because `RequestCommitments::<T>::get(commitment).claimed == true`, no future `accumulate` call will ever select this commitment (filtered out per [2](#0-1) ), so the added `amount` inside `RELAYER_FEE_ACCOUNT` can never be attributed to any relayer or refunded to the payer — it is permanently locked, exactly as described in `fund_message`'s own doc comment.

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

**File:** modules/pallets/relayer/src/accumulate.rs (L58-69)
```rust
		// Filter out already-claimed / missing commitments
		withdrawal_proof.commitments = withdrawal_proof
			.commitments
			.into_iter()
			.filter(|req| match RequestCommitments::<T>::get(*req) {
				Some(leaf_meta) => !leaf_meta.claimed,
				// If request commitment does not exist in storage which should not be
				// possible, we skip it
				None => false,
			})
			.collect();
		ensure!(!withdrawal_proof.commitments.is_empty(), Error::<T>::MissingCommitments);
```

**File:** modules/pallets/relayer/src/accumulate.rs (L149-161)
```rust
		for req in withdrawal_proof.commitments {
			if !claimed_commitments.contains(&req) {
				continue;
			}
			match RequestCommitments::<T>::get(req) {
				Some(mut leaf_meta) => {
					leaf_meta.claimed = true;
					RequestCommitments::<T>::insert(req, leaf_meta)
				},
				// Unreachable
				None => {},
			}
		}
```
