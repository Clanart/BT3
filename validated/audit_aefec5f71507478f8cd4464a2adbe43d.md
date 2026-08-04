### Title
Fisherman veto rewinds `LatestStateMachineHeight` to a `PreviousStateMachineHeight` whose commitment may already be evicted, without any existence/validity check - (File: `modules/pallets/ismp/src/host.rs`)

### Summary
`delete_state_commitment` (the veto/fisherman path that removes a fraudulent state commitment) blindly resets `LatestStateMachineHeight` back to whatever is stored in `PreviousStateMachineHeight`, without checking that a valid commitment for that height still exists in `BoundedStateCommitments`. This is the same "restore from an unverified pointer" defect as the reported `fallback_backup_kernel` bug: a value is copied/restored purely because a reference to it exists, with no check that the referenced data is actually present and valid.

### Finding Description
`store_latest_commitment_height` maintains a single-slot "previous" pointer: [1](#0-0) 

Every time a new height is stored for a state machine, `PreviousStateMachineHeight` is overwritten with whatever was the prior `LatestStateMachineHeight` — it does not track "the highest height that still has a live commitment entry," it only tracks "the height that was previously considered latest."

Separately, `insert_bounded_state_commitment` maintains a FIFO queue per state machine and evicts the oldest commitments once a configurable cap is exceeded: [2](#0-1) 

The cap (`StateCommitmentCap` / `MAX_STATE_MACHINE_COMMITMENTS`) can be lowered by governance via `update_commitment_caps`, and eviction runs independently of the `LatestStateMachineHeight`/`PreviousStateMachineHeight` bookkeeping — it is purely insertion-order based.

When a fisherman vetoes the current latest height, `delete_state_commitment` rewinds the pointer: [3](#0-2) 

It fetches `PreviousStateMachineHeight` and installs it as the new `LatestStateMachineHeight` — with **no check that `BoundedStateCommitments::get(height.id, prev_height)` is `Some`**. The pallet's own code comment even acknowledges the underlying queue/pointer mismatch as a "wart" that burns queue slots, but treats it as a bookkeeping nuisance rather than a correctness problem for the commitment itself. If the cap has been lowered, or enough honest updates landed between the time `prev_height` was recorded and the time the veto is processed, the commitment at `prev_height` will already have been evicted from `BoundedStateCommitments`. The pallet-level unit test `vetoed_latest_height_that_is_resubmitted_evicts_one_insertion_early` demonstrates the queue can already desync after a single veto: [4](#0-3) 

That test only exercises the "one extra queue slot burned" case; it does not cover the case where the cap is small enough (or enough honest submissions arrive) that `PreviousStateMachineHeight` itself is already fully evicted by the time of veto. In that scenario `LatestStateMachineHeight` is set to a height with **no corresponding entry in `BoundedStateCommitments`/`BoundedStateMachineUpdateTime`** — the pointer is dangling, mirroring `fallback_backup_kernel` blindly restoring from `BACKUP_KERNEL_ROOT_HASH` without checking it is populated.

### Impact Explanation
`LatestStateMachineHeight` is the monotonicity anchor consensus-update handling relies on to decide whether an incoming state commitment for a given height is "new" (i.e., whether it should be accepted, and whether the challenge-period clock restarts for it). Rewinding this pointer to a height whose actual commitment record no longer exists breaks the invariant that "latest height implies a retrievable, validated `StateCommitment`" everywhere else in the codebase relies on (e.g. `state_machine_commitment` lookups used by request/response/timeout handlers return `StateCommitmentNotFound`). More seriously, because the rewind is purely numeric and not tied to actual stored state, it reopens the door for a state height that had already been correctly superseded and pruned to be treated as "not yet finalized" again — i.e., height ordering guarantees the consensus-update path depends on to reject stale/duplicate state submissions can be defeated, risking acceptance of an out-of-date state commitment as the current one. This falls squarely under "false proof/state acceptance" in the bounty scope, since it corrupts the trusted state-height bookkeeping that request/response verification and timeouts key off of.

### Likelihood Explanation
Triggering this does not require a malicious relayer, prover, or leaked key — it only requires the normal, permissionless fisherman/veto flow (deleting a state commitment that is later determined invalid) combined with the pallet's own supported governance knob `update_commitment_caps`, which can lower the per-chain commitment retention cap. A small cap plus ordinary chain activity between the previous-height bookmark and the veto is enough to desync `PreviousStateMachineHeight` from `BoundedStateCommitments`; this is not an exotic edge case but a direct, code-comment-acknowledged consequence of how the two data structures are kept (one FIFO-evicted, one single-slot pointer) without cross-validation.

### Recommendation
Before resetting `LatestStateMachineHeight` to `PreviousStateMachineHeight` in `delete_state_commitment`, verify that `BoundedStateCommitments::get(height.id, prev_height)` (and the corresponding update-time entry) actually exists. If it does not, walk back further (e.g., to the highest height still present in `BoundedStateCommitments`) or explicitly reset to a sentinel "no committed height" state and require an honest resubmission from a verified height rather than trusting an unvalidated bookmark.

### Proof of Concept
1. Governance calls `Pallet::update_commitment_caps` to lower the cap for state machine `id` to a small value (e.g. 2), as already exercised in `vetoed_latest_height_that_is_resubmitted_evicts_one_insertion_early`.
2. Store several honest state commitments for `id` in sequence (heights 10, 11, 12, 13, …) via the normal consensus-update path, each call updating `PreviousStateMachineHeight`/`LatestStateMachineHeight` and pushing into the bounded FIFO queue, evicting the oldest entries once the cap is exceeded — so by the time height 13 is latest, the commitment for height 12 (the recorded "previous") has already been evicted from `BoundedStateCommitments`.
3. A fisherman vetoes height 13 via `delete_state_commitment`.
4. `delete_state_commitment` reads `PreviousStateMachineHeight = 12` and sets `LatestStateMachineHeight = 12`, even though `BoundedStateCommitments::get(id, 12)` is now `None`.
5. Any subsequent code path relying on `state_machine_commitment(height=12)` fails with `StateCommitmentNotFound`, while consensus-update logic that gates new submissions on "height > latest (=12)" now permits re-submission/reprocessing of heights the network had already moved past (e.g., a stale or previously-superseded commitment for height 12 or 13), defeating the monotonicity guarantee the veto mechanism is supposed to preserve. [5](#0-4) [6](#0-5)

### Citations

**File:** modules/pallets/ismp/src/host.rs (L194-234)
```rust
	fn delete_state_commitment(&self, height: StateMachineHeight) -> Result<(), Error> {
		// The height's entry in the state commitment queue is deliberately left
		// behind; locating it would mean scanning the queue, which is the per-insert
		// cost the queue exists to avoid. Usually its eviction is a no-op, but when
		// the vetoed height is the latest the reset below re-opens it for honest
		// resubmission, and the resubmitted height gets a *second* queue entry. The
		// stale entry then evicts the live commitment when it reaches the head —
		// one insertion before the live entry would have, since the resubmission
		// lands directly behind its stale twin. So a veto costs that height one
		// insertion of retention and permanently burns one queue slot. Both are
		// negligible against the configured caps; making it exact would need a
		// height -> index map on the insert path.
		BoundedStateCommitments::<T>::remove(height.id, height.height);
		BoundedStateMachineUpdateTime::<T>::remove(height.id, height.height);

		// technically any state commitment can be vetoed,
		// safety check that it's the latest before resetting it.
		if let Some(latest) = LatestStateMachineHeight::<T>::get(height.id) {
			if latest == height.height {
				// Reset back to the initial height to allow for honest updates
				let prev_height =
					PreviousStateMachineHeight::<T>::get(height.id).ok_or_else(|| {
						Error::Custom("Previous state machine height should exist".to_string())
					})?;
				LatestStateMachineHeight::<T>::insert(height.id, prev_height);
			}
		}
		Ok(())
	}

	fn freeze_consensus_client(&self, client: ConsensusStateId) -> Result<(), Error> {
		FrozenConsensusClients::<T>::insert(client, true);
		Ok(())
	}

	fn store_latest_commitment_height(&self, height: StateMachineHeight) -> Result<(), Error> {
		let previous_height = LatestStateMachineHeight::<T>::get(height.id).unwrap_or_default();
		PreviousStateMachineHeight::<T>::insert(height.id, previous_height);
		LatestStateMachineHeight::<T>::insert(height.id, height.height);
		Ok(())
	}
```

**File:** modules/pallets/ismp/src/lib.rs (L740-770)
```rust
		/// Insert a state commitment into the bounded map. ISMP does not allow
		/// duplicate state updates so we don't have an overwrite path.
		///
		/// Appends the height to the per-chain [`StateCommitmentQueue`] and, once
		/// the chain's cap is exceeded, evicts oldest-first from the queue head.
		/// Evictions are limited to [`MAX_COMMITMENT_EVICTIONS_PER_INSERT`] per
		/// call so a lowered cap drains over many insertions rather than in one.
		pub fn insert_bounded_state_commitment(
			height: StateMachineHeight,
			commitment: StateCommitment,
		) {
			let cap = Self::state_machine_commitment_cap(height.id).max(1) as u64;
			let mut state = CommitmentQueueStates::<T>::get(height.id);

			StateCommitmentQueue::<T>::insert(height.id, state.tail, height.height);
			state.tail += 1;

			let excess = (state.tail - state.head)
				.saturating_sub(cap)
				.min(MAX_COMMITMENT_EVICTIONS_PER_INSERT as u64);
			for _ in 0..excess {
				if let Some(old) = StateCommitmentQueue::<T>::take(height.id, state.head) {
					BoundedStateCommitments::<T>::remove(height.id, old);
					BoundedStateMachineUpdateTime::<T>::remove(height.id, old);
				}
				state.head += 1;
			}

			CommitmentQueueStates::<T>::insert(height.id, state);
			BoundedStateCommitments::<T>::insert(height.id, height.height, commitment);
		}
```

**File:** modules/pallets/testsuite/src/tests/pallet_ismp.rs (L829-882)
```rust
// Vetoing the *latest* height resets the latest pointer, which re-opens that height
// for honest resubmission. The resubmission gets a second queue entry, and the stale
// twin ahead of it evicts the live commitment one insertion early. This pins that
// wart: it costs the height one insertion of retention and burns one queue slot,
// which is negligible against the configured caps but is not a no-op.
#[test]
fn vetoed_latest_height_that_is_resubmitted_evicts_one_insertion_early() {
	let mut ext = new_test_ext();
	ext.execute_with(|| {
		let host = Ismp::default();
		let id = queue_test_state_machine();
		let store = |height: u64| {
			host.store_state_machine_commitment(
				StateMachineHeight { id, height },
				queue_test_commitment(),
			)
			.unwrap();
			host.store_latest_commitment_height(StateMachineHeight { id, height }).unwrap();
		};

		pallet_ismp::Pallet::<Test>::update_commitment_caps(
			RuntimeOrigin::root(),
			BTreeMap::from([(id, 2)]),
		)
		.unwrap();

		store(10);
		store(11);

		// Vetoing the latest height rolls the latest pointer back to 10, so the
		// consensus handler would accept 11 again: it is not below the latest and
		// its commitment is now absent.
		host.delete_state_commitment(StateMachineHeight { id, height: 11 }).unwrap();
		assert_eq!(host.latest_commitment_height(id).unwrap(), 10);

		store(11);
		assert!(host.state_machine_commitment(StateMachineHeight { id, height: 11 }).is_ok());
		// Two queue entries now point at height 11: the stale one and the live one.
		assert_eq!(StateCommitmentQueue::<Test>::get(id, 1), Some(11));
		assert_eq!(StateCommitmentQueue::<Test>::get(id, 2), Some(11));

		// Evicting the stale entry at index 1 deletes the live commitment for 11,
		// one insertion before the entry at index 2 would have.
		store(12);
		assert!(host.state_machine_commitment(StateMachineHeight { id, height: 11 }).is_err());
		assert!(host.state_machine_commitment(StateMachineHeight { id, height: 12 }).is_ok());
		// The queue still counts index 2 as live, so the burnt slot leaves this
		// chain retaining one fewer commitment than its cap of 2.
		assert_eq!(
			CommitmentQueueStates::<Test>::get(id),
			CommitmentQueueState { head: 2, tail: 4 }
		);
		assert_eq!(StateCommitmentQueue::<Test>::get(id, 2), Some(11));
	})
```
