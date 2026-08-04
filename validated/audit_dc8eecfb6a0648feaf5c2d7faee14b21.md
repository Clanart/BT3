Based on my investigation, I found a genuine local analog: the EVM host's state-commitment veto path resets the "latest height" bookkeeping to a hardcoded value instead of restoring it to the actual prior trusted height, exactly the missing "restore per-entity state on reset" defect class that M-10 describes for `prepForZeroHeightGenesis`.

### Title
Incomplete state restoration in `EvmHost.deleteStateMachineCommitmentInternal` resets `_latestStateMachineHeight` to a hardcoded value instead of the real previous height - (File: evm/src/core/EvmHost.sol)

### Summary
When a state machine commitment is vetoed (deleted) on the EVM host, the "latest height" pointer for that state machine is unconditionally reset to `1` rather than to the actual previously-trusted height, unlike the Substrate `pallet-ismp` implementation of the same operation, which correctly restores `LatestStateMachineHeight` from a tracked `PreviousStateMachineHeight` value.

### Finding Description
`EvmHost.deleteStateMachineCommitmentInternal` deletes the vetoed commitment and, if it was the latest height, resets the pointer to a magic constant: [1](#0-0) 

Compare this to the Substrate host implementation of the equivalent operation, which explicitly restores the prior height from a dedicated `PreviousStateMachineHeight` storage item that is maintained on every commitment update: [2](#0-1) [3](#0-2) 

The EVM `EvmHost.sol` contract has no equivalent of `PreviousStateMachineHeight`/`store_latest_commitment_height` bookkeeping at all — a repo-wide search for this pattern under `evm/` found no matches, confirming the step present in the Substrate reference is simply absent on the EVM side. This is the same bug class as the report: a reset/cleanup routine that omits a necessary restoration step present in the reference implementation, leaving stale/inconsistent state after the reset.

The consequence surfaces in `HandlerV2.handleConsensus`, which is the sole gate for accepting a new state-machine height as the trusted "latest": [4](#0-3) 

The acceptance condition is only `latestHeight != 0 && intermediate.height > latestHeight`. After a veto resets the pointer to `1`, this check no longer reflects the chain's true previously-trusted height (which may be arbitrarily larger, since only the single vetoed height's own commitment entry was deleted — all other, unrelated heights remain stored via `_stateCommitments`). Any subsequently delivered height greater than `1` is now accepted as "advancing" and overwrites `_latestStateMachineHeight` for that state machine, even though it may be far below the real prior watermark.

### Impact Explanation
This breaks the pivot invariant that "state commitments must never let false remote state become trusted." Once the latest-height watermark for a state machine is silently rolled back to `1` instead of the genuine last-trusted height, an out-of-order or stale intermediate state (older, lower height) can be re-accepted as the new "latest" commitment for that chain, corrupting `_latestStateMachineHeight` and diverging it from the actual monotonic history the protocol assumes. Because downstream logic (challenge-period timing, timeout dispatch, and future consensus advancement checks) relies on this watermark being monotonically correct, this creates a path toward false-state acceptance for that state machine after any veto event, undermining the very commitment-integrity guarantee the veto mechanism exists to protect.

### Likelihood Explanation
The veto/delete path is only reachable through `_hostParams.handler` (i.e., following a legitimate fisherman-driven veto of a bad commitment), so triggering the reset itself is not an arbitrary-attacker action. However, once a veto legitimately occurs (part of normal protocol operation, not an attacker exploit), every subsequent honest or dishonest consensus update for that state machine is silently exposed to this incorrect, hardcoded watermark, with no additional privilege required to submit the next (now-accepted) update. I could not fully verify from local code alone whether the underlying consensus client's own monotonicity checks (e.g., BEEFY's own `latestHeight` in `EcdsaBeefy`) would independently prevent a materially out-of-order height from reaching `HandlerV2` in practice; this remains an open question requiring further protocol-level analysis or testing.

### Recommendation
Add a `_previousStateMachineHeight` mapping to `EvmHost`, updated on every `storeStateMachineCommitment` call the same way `store_latest_commitment_height` does in `modules/pallets/ismp/src/host.rs`, and have `deleteStateMachineCommitmentInternal` restore `_latestStateMachineHeight` from that tracked value instead of the hardcoded `1`, mirroring the Substrate reference implementation exactly.

### Proof of Concept
1. Host has `_latestStateMachineHeight[id] = 5_000_000` after normal operation.
2. A fisherman proves height `5_000_000`'s commitment for `id` is invalid; the handler calls `deleteStateMachineCommitment`, which runs `deleteStateMachineCommitmentInternal` — since `5_000_000` was the latest, `_latestStateMachineHeight[id]` is reset to `1` (not to the real prior trusted height, e.g. `4_999_500`). [5](#0-4) 
3. Any subsequent `handleConsensus` call carrying an intermediate state for `id` at any height `> 1` (e.g., height `10`, far below the chain's real history) now satisfies `latestHeight != 0 && intermediate.height > latestHeight` and is accepted as the new trusted commitment via `storeStateMachineCommitment`, overwriting the watermark with a value the protocol never intended to treat as "latest." [6](#0-5)

### Citations

**File:** evm/src/core/EvmHost.sol (L714-724)
```text
    function deleteStateMachineCommitmentInternal(StateMachineHeight memory height, address fisherman) internal {
        StateCommitment memory stateCommitment = _stateCommitments[height.stateMachineId][height.height];
        delete _stateCommitments[height.stateMachineId][height.height];
        delete _stateCommitmentsUpdateTime[height.stateMachineId][height.height];
        // technically any state commitment can be vetoed, safety check that it's the latest before resetting it.
        if (_latestStateMachineHeight[height.stateMachineId] == height.height) {
            _latestStateMachineHeight[height.stateMachineId] = 1;
        }

        // track the fisherman responsible for rewards on hyperbridge through state proofs
        _vetoes[height.stateMachineId][height.height] = fisherman;
```

**File:** modules/pallets/ismp/src/host.rs (L194-221)
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
```

**File:** modules/pallets/ismp/src/host.rs (L229-234)
```rust
	fn store_latest_commitment_height(&self, height: StateMachineHeight) -> Result<(), Error> {
		let previous_height = LatestStateMachineHeight::<T>::get(height.id).unwrap_or_default();
		PreviousStateMachineHeight::<T>::insert(height.id, previous_height);
		LatestStateMachineHeight::<T>::insert(height.id, height.height);
		Ok(())
	}
```

**File:** evm/src/core/HandlerV2.sol (L155-164)
```text
        uint256 intermediatesLen = intermediates.length;
        for (uint256 i = 0; i < intermediatesLen; i++) {
            IntermediateState memory intermediate = intermediates[i];
            uint256 latestHeight = host.latestStateMachineHeight(intermediate.stateMachineId);
            if (latestHeight != 0 && intermediate.height > latestHeight) {
                StateMachineHeight memory stateMachineHeight =
                    StateMachineHeight({stateMachineId: intermediate.stateMachineId, height: intermediate.height});
                host.storeStateMachineCommitment(stateMachineHeight, intermediate.commitment);
            }
        }
```
