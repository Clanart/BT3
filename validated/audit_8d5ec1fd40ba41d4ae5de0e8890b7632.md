Based on the code I was able to inspect, here's my assessment:

## Title
Bounded FIFO update-time queue can evict update-time entries for heights still backing pending, challenge-period-gated settlements - (File: `modules/pallets/ismp/src/host.rs`)

### Summary
`state_machine_update_time` (host.rs:73-85) is the sole source of truth used to compute whether a state commitment's challenge period has elapsed, and it fails closed with `Error::Custom("Update time not found...")` if the entry for that height is missing. [1](#0-0) 
The entry is written by `store_state_machine_update_time`, which simply delegates to `Pallet::<T>::insert_bounded_update_time`, an operation on a size-bounded structure (`BoundedStateMachineUpdateTime`). [2](#0-1) 

### Finding Description
The pallet's own code comment on the sibling function `delete_state_commitment` explicitly documents that the bounded queues (`BoundedStateCommitments` / `BoundedStateMachineUpdateTime`) are simple bounded/ring structures that evict old entries strictly by insertion order ("queue exists to avoid" per-insert scans, entries evicted "when it reaches the head"), independent of whether that entry is still semantically needed by an in-flight settlement. [3](#0-2) 
This confirms the queue eviction mechanism is FIFO-by-insertion, not usage-aware. If enough new heights are inserted for the same `StateMachineId` before an older height's dependent claim clears its `challenge_period`, that older height's update-time record is evicted, and any later proof verification relying on it via `state_machine_update_time` will hard-fail rather than succeed, permanently blocking that claim's settlement.

### Impact Explanation
If exploitable, this stalls legitimate withdrawals/claims/fills permanently once their backing height's update-time entry is evicted, matching the "permanently lock or burn user funds" impact family called out in scope.

### Likelihood Explanation
I could not confirm two critical facts needed to assess real-world exploitability, because I ran out of tool iterations before locating `insert_bounded_update_time`'s implementation in `modules/pallets/ismp/src/lib.rs` (only grep hits were found, not the function body):
1. The actual bound/cap size of the queue relative to realistic challenge-period durations.
2. Whether the queue is keyed/bounded per `StateMachineId` or globally, and whether inserting a new height for the *same* `StateMachineId` requires a genuinely new, validly-proven consensus update (which depends on real external chain progression and consensus-client proof verification) or can be triggered cheaply/rapidly by any unprivileged caller.

Consensus updates are gated by the consensus client's own proof verification (e.g., valid signed finality proofs), so "attacker-triggerable" here likely means permissionlessly *relaying already-valid, unsubmitted* consensus proofs for many distinct heights in rapid succession — which is plausible without needing a malicious relayer/node, but the practical rate is bounded by how many distinct valid, not-yet-relayed heights exist and by the queue's actual cap size, neither of which I was able to verify.

### Recommendation
Given the acknowledged FIFO eviction design (already flagged as a known tradeoff in the code comments), consider either sizing the bound generously relative to the maximum configured `challenge_period`, or tracking a "high-water mark"/pinning mechanism so that heights with pending dependent settlements cannot be evicted prematurely. This should be verified against the actual constant used for the bound and the max configured challenge period.

### Proof of Concept
Not fully verifiable within the available context — would require the full `insert_bounded_update_time` implementation and the configured bound size in `modules/pallets/ismp/src/lib.rs`, which I could not retrieve within my tool-call budget. A Devin session with full repository access would be needed to confirm the queue's cap constant and validate whether the eviction rate under realistic relaying conditions can outpace a live challenge period before a pending claim settles.

**Caveat:** Because I could not confirm the queue capacity constant or the exact per-`StateMachineId` insertion semantics, I cannot definitively confirm this as an exploitable vulnerability versus a defensively-sized bound that makes the scenario impractical. I recommend treating this as a **plausible but unconfirmed** finding pending verification of the bound size in `modules/pallets/ismp/src/lib.rs`.

### Citations

**File:** modules/pallets/ismp/src/host.rs (L73-85)
```rust
	fn state_machine_update_time(
		&self,
		state_machine_height: StateMachineHeight,
	) -> Result<Duration, Error> {
		BoundedStateMachineUpdateTime::<T>::get(
			state_machine_height.id,
			state_machine_height.height,
		)
		.map(|timestamp| Duration::from_secs(timestamp))
		.ok_or_else(|| {
			Error::Custom(format!("Update time not found for {:?}", state_machine_height))
		})
	}
```

**File:** modules/pallets/ismp/src/host.rs (L175-183)
```rust
	fn store_state_machine_update_time(
		&self,
		state_machine_height: StateMachineHeight,
		timestamp: Duration,
	) -> Result<(), Error> {
		let ts = timestamp.as_secs().saturated_into::<u64>();
		Pallet::<T>::insert_bounded_update_time(state_machine_height, ts);
		Ok(())
	}
```

**File:** modules/pallets/ismp/src/host.rs (L194-222)
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
```
