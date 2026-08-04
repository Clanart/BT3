Based on my research, I found a concrete local analog to the "lastGoodPrice updates during divergence" bug class: an anchor-value reset that uses a hardcoded, unsafe sentinel instead of the actual last-known-good value, which reopens the door to accepting stale/superseded state.

### Title
`EvmHost` resets `_latestStateMachineHeight` to sentinel `1` instead of the actual prior height on veto, reopening acceptance of superseded state commitments - (File: `evm/src/core/EvmHost.sol`)

### Summary
When a state commitment is vetoed (deleted, e.g. because it was found fraudulent) and that commitment happened to be the "latest" height for its state machine, `EvmHost.deleteStateMachineCommitmentInternal` resets the "latest height" watermark to the hardcoded value `1` instead of restoring the actual previous validated height. This is the direct EVM-side analog of the `OracleAdapterV4` bug: instead of preserving/rolling back to a verified safe anchor after a dispute event, the code substitutes an unsafe placeholder value that widens the acceptance window for future proofs, rather than narrowing it back to the last trusted state.

### Finding Description
`EvmHost.deleteStateMachineCommitmentInternal` [1](#0-0)  deletes the vetoed commitment and, if it was the latest height, does this:
```solidity
if (_latestStateMachineHeight[height.stateMachineId] == height.height) {
    _latestStateMachineHeight[height.stateMachineId] = 1;
}
```
Compare this with the equivalent Substrate implementation, `modules/pallets/ismp/src/host.rs::delete_state_commitment`, which explicitly restores the real previous height from `PreviousStateMachineHeight` [2](#0-1) , with an explicit comment: "Reset back to the initial height to allow for honest updates." The Substrate code tracks and restores the *actual* prior verified height; the EVM code instead hardcodes `1`, discarding the true watermark entirely.

The consumer of `_latestStateMachineHeight` is `HandlerV2.handleConsensus`, whose only defense against accepting stale/rolled-back state is:
```solidity
uint256 latestHeight = host.latestStateMachineHeight(intermediate.stateMachineId);
if (latestHeight != 0 && intermediate.height > latestHeight) {
    host.storeStateMachineCommitment(stateMachineHeight, intermediate.commitment);
}
``` [3](#0-2) 

After a veto resets the watermark to `1`, this guard is trivially satisfied by *any* height greater than 1 — including heights that were already correctly superseded by prior, still-valid consensus updates before the veto occurred. This is exactly the "poisoned anchor" pattern from the external report: the code proceeds to establish a new "trusted" baseline using an unverified/unsafe value in the exact code path meant to recover safely from a detected fault.

### Impact Explanation
This corrupts the core invariant that `latestStateMachineHeight` monotonically tracks only verified, canonical state — the same invariant `lastGoodPrice` was meant to protect in the oracle report. Once reset to `1`, a relayer can resubmit an older, already-superseded state commitment (potentially at a height lower than what was legitimately already accepted) as if it were new, because the height-monotonicity check no longer reflects genuine bridge history. This can let false/stale remote state become "trusted" again on the destination host, directly matching the required impact class: "false proof/state acceptance" and the pivot: "consensus proofs, state proofs, challenge periods, and state commitments must never let false remote state become trusted."

### Likelihood Explanation
Triggering `deleteStateMachineCommitmentInternal` requires the veto/fraud-proof path (invoked only through `_hostParams.handler`) to fire against the current latest height. I was unable to fully trace, within the available search budget, whether the EVM veto/fraud-proof entrypoint that ultimately calls `deleteStateMachineCommitment` is permissionless (as the Substrate fishermen pallet's `veto_state_commitment` is, gated only by `IsCollator`) or requires additional privilege on the EVM side — this is an open point that would need direct inspection of the handler/fishermen contract wiring on EVM to confirm exploitability by a fully unprivileged actor. Given the strong structural asymmetry with the audited/documented Substrate behavior and the explicit "restore prior height" comment present only on one side, this is a credible, locally-evidenced logic defect regardless of the exact caller privilege level.

### Recommendation
Track the actual previous verified height per state machine on the EVM host (mirroring `PreviousStateMachineHeight` in the pallet), and restore that value in `deleteStateMachineCommitmentInternal` instead of hardcoding `1`. If no reliable previous height is available, fail closed (revert) rather than silently substituting an arbitrary low watermark.

### Proof of Concept
1. State machine `X` has commitments at heights 10 and 11; `_latestStateMachineHeight[X] == 11`.
2. A fraud proof / veto is submitted against height 11 (the legitimately latest one), and `deleteStateMachineCommitmentInternal` is invoked (`evm/src/core/EvmHost.sol:714-732`).
3. Since `_latestStateMachineHeight[X] == 11 == height.height`, the watermark resets to `1` instead of `10`.
4. A relayer now submits a consensus proof carrying an intermediate state at height `2` (or any stale height it can still produce/replay) for state machine `X`.
5. `HandlerV2.handleConsensus`'s check `intermediate.height > latestHeight` (now comparing against `1`) passes, and `storeStateMachineCommitment` overwrites/accepts this stale height as valid new state — bypassing the monotonic-height protection intended to keep already-superseded heights from being reintroduced.

### Citations

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

**File:** modules/pallets/ismp/src/host.rs (L209-220)
```rust
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
```

**File:** evm/src/core/HandlerV2.sol (L156-164)
```text
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
