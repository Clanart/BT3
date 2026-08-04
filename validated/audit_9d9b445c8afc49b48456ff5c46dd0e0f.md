### Title
State-commitment veto resets `_latestStateMachineHeight` to a hardcoded `1` instead of the true prior height, reopening already-finalized heights to silent overwrite via permissionless `handleConsensus` - ([File: evm/src/core/EvmHost.sol])

### Summary
This is the same bug class as the `canOffboard[term]` re-trigger issue: a "cleanup"/veto action that is supposed to close out state instead resets a guard variable to an overly permissive value, which lets a later, ordinary permissionless call re-trigger privileged storage writes that should have remained locked. Here the guard is `_latestStateMachineHeight`, the veto is `deleteStateMachineCommitment` (called for fisherman state-commitment vetoes), and the re-triggering call is the fully permissionless `HandlerV2.handleConsensus`.

### Finding Description
`EvmHost.deleteStateMachineCommitmentInternal` deletes a vetoed `StateCommitment`, and if the vetoed height happened to be the current latest height, it "resets" the latest-height pointer: [1](#0-0) 

Unlike the Substrate host (`pallet-ismp`), which rolls the pointer back to the **actual** previous height via `PreviousStateMachineHeight`: [2](#0-1) 

the EVM host hardcodes the rollback target to the literal constant `1`, regardless of what heights are actually still validly stored (e.g. 10, 11 could still be present in `_stateCommitments` while `_latestStateMachineHeight` is forced down to `1`).

That pointer is the only gate `HandlerV2.handleConsensus` uses before accepting a new state-machine commitment for a given height: [3](#0-2) 

Note two things that differ from the Substrate `update_client` equivalent:
1. The guard is `intermediate.height > latestHeight` — after a veto this becomes `> 1`, i.e. almost any height qualifies.
2. There is **no "already has a stored commitment, skip" check** the way Substrate's handler has (`if host.state_machine_commitment(state_height).is_ok() { continue; }`): [4](#0-3) 

Because of (2), once (1) is satisfied for a height that already has a live, previously-accepted `StateCommitment` (e.g. height 10 or 11 in the example above), `storeStateMachineCommitment` unconditionally overwrites `_stateCommitments[id][height]` with whatever commitment the newly-verified consensus proof produced for that same height number, and also stamps a fresh `_stateCommitmentsUpdateTime[id][height] = block.timestamp`: [5](#0-4) 

`handleConsensus` is explicitly permissionless ("Access: Permissionless (can be called by anyone)") and the veto (`deleteStateMachineCommitment`) is performed by a fisherman, a permissionless watchdog role, not an admin/governance actor — so the whole chain from "veto" to "re-acceptance of a previously-finalized height" requires no privileged or malicious party, mirroring the offboarding poll's re-triggerable flag.

### Impact Explanation
This breaks the "state commitments must never let false remote state become trusted" invariant: once the latest-height gate is collapsed to `1` by a single legitimate veto of the current tip, a subsequent ordinary consensus proof (which, for BEEFY/GRANDPA-style clients, routinely reports several intermediate parachain heights per finality proof) can silently rewrite the `StateCommitment` stored at a height that relayers, apps, and the SDK already treat as finalized and challenge-period-cleared. This:
- Resets that height's `_stateCommitmentsUpdateTime`, restarting its `challengePeriod` clock and enabling denial-of-service against pending `handlePostRequests`/`handleGetResponses`/timeout proofs that were relying on that height already having cleared its challenge window.
- Allows the overlay/state root recorded for an already-relied-upon height to be replaced with a different value from a later (potentially stale or differently-derived) proof, undermining the fixed mapping between a height and its committed state that downstream request/response Merkle proofs assume.
- Regresses `_latestStateMachineHeight` in a way inconsistent with the actual set of stored heights, corrupting the monotonicity invariant the whole ISMP pipeline depends on for challenge-period sequencing.

### Likelihood Explanation
The trigger sequence — (a) a fisherman vetoing the current-tip commitment (an intended, permissionless protocol action for faulty commitments), followed by (b) any relayer submitting a subsequent, otherwise-valid consensus proof whose intermediate-state list includes a height at or below the pre-veto tip — is a normal operational sequence, not an edge case requiring a malicious peer, relayer, or governance actor. It only requires that the veto target the current latest height and that a later legitimate consensus update reports overlapping intermediate heights, both realistic given BEEFY/GRANDPA proofs typically batch multiple recent parachain heights.

### Recommendation
- In `EvmHost.deleteStateMachineCommitmentInternal`, roll `_latestStateMachineHeight[id]` back to the actual previous valid height (track and use a `previousStateMachineHeight` mapping, as pallet-ismp does), not the hardcoded value `1`.
- In `HandlerV2.handleConsensus`, add the missing "skip if a commitment already exists at this height" check before calling `storeStateMachineCommitment`, matching the Substrate `update_client` semantics, so a height that has already been finalized can never be silently overwritten by a later consensus proof.

### Proof of Concept
1. Consensus client delivers a proof whose intermediates finalize heights 10 and 11; `_latestStateMachineHeight[id] = 11`, both `_stateCommitments[id][10]` and `[id][11]` are populated.
2. A fisherman detects the commitment at height 11 is wrong and calls `deleteStateMachineCommitment({id, 11}, fisherman)`. Since `11 == _latestStateMachineHeight[id]`, the pointer is force-reset to `1` (`EvmHost.sol:719-721`), even though height 10's commitment is still intact and trusted.
3. Any permissionless caller submits `handleConsensus` with a fresh, otherwise-legitimate proof whose intermediate list again includes height 10 (e.g. re-derived from a re-org-free but overlapping BEEFY MMR batch). The guard `intermediate.height(10) > latestHeight(1)` passes (`HandlerV2.sol:158-163`), and there is no "already stored" check, so `storeStateMachineCommitment({id,10}, newCommitment)` overwrites the previously trusted commitment at height 10 and resets its `_stateCommitmentsUpdateTime`, restarting the challenge period for a height apps had already begun relying on.

### Citations

**File:** evm/src/core/EvmHost.sol (L683-699)
```text
    /**
     * @dev Store the state commitment at given state height alongside relevant metadata.
     * Assumes the state commitment is of the latest height.
     */
    function storeStateMachineCommitment(StateMachineHeight memory height, StateCommitment memory commitment)
        external
        restrict(_hostParams.handler)
    {
        _stateCommitments[height.stateMachineId][height.height] = commitment;
        _stateCommitmentsUpdateTime[height.stateMachineId][height.height] = block.timestamp;
        _latestStateMachineHeight[height.stateMachineId] = height.height;

        emit StateMachineUpdated({
            stateMachineId: this.stateMachineId(_hostParams.hyperbridge, height.stateMachineId), 
            height: height.height
        });
    }
```

**File:** evm/src/core/EvmHost.sol (L714-732)
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

        emit StateCommitmentVetoed({
            stateMachineId: this.stateMachineId(_hostParams.hyperbridge, height.stateMachineId),
            stateCommitment: stateCommitment,
            height: height.height,
            fisherman: fisherman
        });
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

**File:** evm/src/core/HandlerV2.sol (L144-174)
```text
    function handleConsensus(IHost host, bytes calldata proof) external notFrozen(host) {
        uint256 delay = block.timestamp - host.consensusUpdateTime();
        if (delay >= host.unStakingPeriod()) revert ConsensusClientExpired();

        bytes memory previousState = host.consensusState();
        (bytes memory verifiedState, IntermediateState[] memory intermediates, uint256 nextAuthoritySetId) =
            IConsensusV2(host.consensusClient()).verify(previousState, proof);

        if (keccak256(previousState) == keccak256(verifiedState)) return;
        host.storeConsensusState(verifiedState);

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

        // `nextAuthoritySetId` identifies the upcoming set; the relayer that delivered the proof
        // is credited as the relayer for the just-ended epoch (`nextAuthoritySetId - 1`).
        // If `nextAuthoritySetId == 0` no rotation has occurred, so there is nothing to record.
        if (nextAuthoritySetId == 0) return;
        uint256 epoch = nextAuthoritySetId - 1;
        if (epoch > host.currentEpoch()) {
            host.recordEpoch(epoch, _msgSender());
        }
    }
```

**File:** modules/ismp/core/src/handlers/consensus.rs (L51-71)
```rust
	for (id, mut commitment_heights) in intermediate_states {
		commitment_heights.sort_unstable_by(|a, b| a.height.cmp(&b.height));
		let previous_latest_height = host.latest_commitment_height(id)?;
		let mut last_commitment_height = None;
		for commitment_height in commitment_heights.iter() {
			let state_height = StateMachineHeight { id, height: commitment_height.height };

			// Only allow heights greater than latest height
			if previous_latest_height > commitment_height.height {
				continue;
			}

			// Skip duplicate states
			if host.state_machine_commitment(state_height).is_ok() {
				continue;
			}

			last_commitment_height = Some(state_height);
			host.store_state_machine_commitment(state_height, commitment_height.commitment)?;
			host.store_state_machine_update_time(state_height, host.timestamp())?;
		}
```
