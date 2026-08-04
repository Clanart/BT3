## Analysis Summary

The Compound bug's core invariant break is: **a value used to make a critical financial decision (liquidatability) is read live/mutable at decision time instead of being pinned to the state that existed when the underlying obligation was created**, letting stale/mismatched data produce a wrong payout/settlement outcome.

I looked for the closest Hyperbridge analog across bandwidth accounting (`pallet-bandwidth`), relayer fee accumulation (`pallet-ismp-relayer::accumulate`), fee withdrawal (`pallet-ismp-relayer::withdraw`), and outbound-request delivery rewards (`pallet-ismp-relayer::outbound_request`). Bandwidth and fee-accumulation both compute their values atomically from live state-proofs or single-storage mutations inside one extrinsic, so they don't reproduce the "stale index used for a critical decision" pattern. The `OutboundRequestDeliveryClaim` path does reproduce it: the reward amount paid out for a **already-completed, historical** delivery is fetched from a **currently mutable governance value** at claim time rather than being bound to the value in force when the request was dispatched/delivered.

### Title
Outbound-request delivery reward is priced at claim time from a live, governance-mutable value instead of the rate in effect when the request was delivered - ([File: modules/pallets/relayer/src/outbound_request.rs])

### Summary
`process_outbound_request_delivery_claim` pays a relayer `OutboundRequestDeliveryReward::<T>::get(&module_id)` — the *current* value of a governance-settable storage item — for proving delivery of a `PostRequest` that may have been dispatched and delivered arbitrarily long ago. The reward is never snapshotted at request-creation or delivery time; it is looked up fresh on every claim. [1](#0-0) 

### Finding Description
`OutboundRequestDeliveryReward` is a per-`module_id` governance parameter, set instantly and without any timelock: [2](#0-1) 

A commitment stays claimable in `RequestCommitments` until `OutboundRequestsClaimed` is set, i.e. indefinitely as a backlog: [3](#0-2) 

The claim path verifies (1) the request's source is Hyperbridge, (2) the commitment exists and is unclaimed, (3) a state proof that the destination's `RequestReceipts[commitment]` was written by the signer, then pays `reward` **read at the moment of the claim call**, not the reward that applied when the request was originally created/delivered. There is no field anywhere in `RequestCommitments`/`PostRequest`/the claim payload that pins the reward rate to dispatch time. This is structurally the same defect as the Compound report: a decision (how much to pay) is made using a value (`OutboundRequestDeliveryReward`) whose freshness is disconnected from the event (the historical delivery) it's supposed to price.

### Impact Explanation
Because unclaimed commitments can sit in the backlog indefinitely, and the reward-per-module value can be raised by governance at any time with immediate effect, every unclaimed historical delivery in that module's backlog is retroactively re-priced at the new rate the instant governance changes it. A relayer holding valid, already-completed delivery proofs is not required to claim promptly; by withholding claims until after an announced/observed reward increase and only then submitting `process_outbound_request_delivery_claim`, the relayer extracts treasury funds at a rate governance never intended to apply to that historical batch of work. This is a direct, wrong-amount fund transfer out of `TreasuryPalletId`'s account — matching the "relayer rewards must move exactly once and only to the rightful beneficiary and amount" pivot — without needing a colluding/malicious governance actor: governance's legitimate rate change is misapplied to a backlog it was never meant to affect.

### Likelihood Explanation
This requires only an ordinary, unprivileged relayer who already delivered requests (a routine, non-adversarial action) and who simply times a normal, public claim extrinsic after observing a public reward-parameter change — no malicious peer, prover, or governance collusion is needed. The reward's immediate, un-timelocked, module-wide effect on an unbounded backlog makes exploitation straightforward and detectable only after funds have moved.

### Recommendation
Snapshot the applicable reward rate (and/or the reward-config version/epoch) into the request commitment metadata at delivery/dispatch time (or at minimum, at the block the request commitment was first inserted into `RequestCommitments`) and use that pinned value in `process_outbound_request_delivery_claim`, rather than re-reading `OutboundRequestDeliveryReward` live. Alternatively, invalidate/expire outstanding claimable commitments whenever the module's reward parameter changes, or require claims to be submitted within a bounded window of delivery so the live-rate lookup cannot diverge meaningfully from the rate that applied when the work was done.

### Proof of Concept
1. Governance sets `OutboundRequestDeliveryReward[moduleX] = 1` (via `set_outbound_request_delivery_reward`).
2. A relayer delivers 100 hyperbridge-originated requests from `moduleX` to a destination chain over time; each writes a `RequestReceipts[commitment]` entry there, and each `commitment` remains unclaimed (`OutboundRequestsClaimed` empty) in `RequestCommitments` on Hyperbridge.
3. The relayer withholds all 100 claims.
4. Governance later raises `OutboundRequestDeliveryReward[moduleX] = 1000` for legitimate, forward-looking reasons (e.g., gas cost increase on the destination chain).
5. The relayer immediately submits `claim_outbound_request_delivery_reward` for all 100 backlogged commitments; `process_outbound_request_delivery_claim` reads the *new* `reward = 1000` for every one of them and transfers `1000 × 100` from the treasury instead of the `1 × 100` that was in effect when the work was actually performed — a 1000x unintended payout that was never approved for that historical batch of deliveries. [4](#0-3)

### Citations

**File:** modules/pallets/relayer/src/outbound_request.rs (L133-141)
```rust
		ensure!(
			RequestCommitments::<T>::get(commitment).is_some(),
			Error::<T>::OutboundRequestNotKnown,
		);

		ensure!(
			!OutboundRequestsClaimed::<T>::contains_key(commitment),
			Error::<T>::OutboundRequestAlreadyClaimed,
		);
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L143-151)
```rust
		let module_id: BoundedVec<u8, ModuleIdBound> = request
			.from
			.clone()
			.try_into()
			.map_err(|_| Error::<T>::OutboundRequestModuleIdTooLong)?;
		let reward = OutboundRequestDeliveryReward::<T>::get(&module_id);
		ensure!(reward > BalanceOf::<T>::default(), Error::<T>::OutboundRequestNoRewardConfigured);

		ensure!(destination == request.dest, Error::<T>::MismatchedStateMachine);
```

**File:** modules/pallets/relayer/src/outbound_request.rs (L175-196)
```rust
		let treasury: T::AccountId =
			<T as Config>::TreasuryPalletId::get().into_account_truncating();
		let payee_account: T::AccountId = payee.into();
		<<T as pallet_ismp::Config>::Currency as Mutate<T::AccountId>>::transfer(
			&treasury,
			&payee_account,
			reward,
			Preservation::Preserve,
		)
		.map_err(|_| Error::<T>::OutboundRequestRewardTransferFailed)?;

		OutboundRequestsClaimed::<T>::insert(commitment, ());

		Self::deposit_event(Event::OutboundRequestDeliveryRewarded {
			commitment,
			state_machine: destination,
			module_id,
			relayer: payee_account,
			amount: reward,
		});

		Ok(())
```

**File:** modules/pallets/relayer/src/lib.rs (L399-415)
```rust
		/// Governance-set per-chain reward for delivering mandatory consensus
		/// proofs to that destination.
		#[pallet::call_index(4)]
		#[pallet::weight(<T as frame_system::Config>::DbWeight::get().reads_writes(0, 1))]
		pub fn set_outbound_consensus_delivery_reward(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			amount: BalanceOf<T>,
		) -> DispatchResult {
			T::RelayerOrigin::ensure_origin(origin)?;
			OutboundConsensusDeliveryReward::<T>::insert(state_machine, amount);
			Self::deposit_event(Event::OutboundConsensusDeliveryRewardUpdated {
				state_machine,
				new_reward: amount,
			});
			Ok(())
		}
```
