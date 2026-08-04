### Title
`EvmHost.deleteStateMachineCommitmentInternal` resets `_latestStateMachineHeight` to a hardcoded `1` instead of the true prior verified height, letting a stale state commitment be re-accepted as latest - (File: `evm/src/core/EvmHost.sol`)

### Summary
This is a local analog of the reported bug class: an address/version pointer (`registry`) is updated but the dependent cache is not rebuilt or checked for consistency, producing outdated/incorrect results. In `EvmHost.sol` the analogous "cache" is `_latestStateMachineHeight[stateMachineId]`, which gates which new consensus-verified heights may be accepted [1](#0-0) . When a fisherman vetoes the *latest* commitment via `deleteStateMachineCommitmentInternal`, the EVM host resets this gate to a hardcoded `1` rather than restoring the actual previous verified height [2](#0-1) .

### Finding Description
The `update_client` handler only accepts a new `StateCommitment` at height `h` for a state machine if `h > previous_latest_height` (the cached latest height) [1](#0-0) . This monotonicity cache is exactly the same kind of cached-derived-value the external report warns about: a value computed from prior state that must stay consistent whenever the underlying source of truth changes (here, whenever a commitment is vetoed).

The Substrate implementation (`pallet-ismp`) correctly tracks and restores the *real* prior height on veto:
```rust
if let Some(latest) = LatestStateMachineHeight::<T>::get(height.id) {
    if latest == height.height {
        let prev_height = PreviousStateMachineHeight::<T>::get(height.id).ok_or_else(...)?;
        LatestStateMachineHeight::<T>::insert(height.id, prev_height);
    }
}
``` [3](#0-2) 

The EVM implementation does **not** track a "previous height" at all, and instead hardcodes the reset to `1`:
```solidity
function deleteStateMachineCommitmentInternal(StateMachineHeight memory height, address fisherman) internal {
    StateCommitment memory stateCommitment = _stateCommitments[height.stateMachineId][height.height];
    delete _stateCommitments[height.stateMachineId][height.height];
    delete _stateCommitmentsUpdateTime[height.stateMachineId][height.height];
    // technically any state commitment can be vetoed, safety check that it's the latest before resetting it.
    if (_latestStateMachineHeight[height.stateMachineId] == height.height) {
        _latestStateMachineHeight[height.stateMachineId] = 1;
    }
    ...
}
``` [4](#0-3) 

This is the exact analog to the reported bug: the cached "latest height" pointer is updated (mutated) by a privileged/semi-trusted actor (a fisherman submitting a fraud proof) without any consistency check against the actual history it is supposed to represent — it is blindly rebuilt to a fixed sentinel value instead of the correct prior state, just like the report's `_updateVaultCache`/`setRegistry` not reconciling the cache with the true registry history.

The consequence: once any veto of the current latest height occurs, the freshness gate collapses to "any height > 1," so a subsequent `update_client` call carrying an already-superseded, lower-height (but validly consensus-signed) `StateCommitment` — e.g., one that a relayer previously obtained before the chain progressed further, or one produced by a stale/rotated validator set that the client would otherwise reject as "not newer than what we've already seen" — can be re-accepted as the "latest" trusted state for that state machine. Since `verify_consensus` for a given consensus client only rejects proofs based on that client's own internal `finalized_height`, not the host's `_latestStateMachineHeight`, the host-side gate is the layer relied upon to prevent regressions after a veto, and it is broken here.

### Impact Explanation
`_stateCommitments[stateMachineId][height]` backs proof verification for all ISMP requests/responses/timeouts routed through that state machine (storage proofs are validated against whichever commitment is considered "latest"/available). Accepting a stale, previously-superseded state root as trusted after a veto is a form of false remote state acceptance — the exact class the bounty scope explicitly protects (`Consensus proofs, state proofs, challenge periods, and state commitments must never let false remote state become trusted`). A stale root could be used to forge storage proofs for requests/responses (e.g., asset withdrawals, token-gateway transfers) that reference balances/state which no longer reflect the real chain, enabling unauthorized execution or fund loss on the EVM side of the bridge.

### Likelihood Explanation
The veto path (`deleteStateMachineCommitment`) is restricted to `_hostParams.handler` [5](#0-4) , and is intended to be triggered by the protocol's fisherman/fraud-proof mechanism — a permissionless, protocol-designed process for challenging bad state, not a "malicious governance actor." Any party capable of submitting a valid fraud proof (an intended, permissionless capability) can trigger the reset; any relayer can then permissionlessly submit a subsequent `update_client` message. No collusion with governance, a prover, or a relayer's private key compromise is required — only use of the fisherman/veto mechanism as designed followed by an ordinary consensus-message submission, both of which are normal, unprivileged protocol interactions.

### Recommendation
Mirror the Substrate implementation: track a `_previousStateMachineHeight` mapping in `EvmHost.sol`, update it on every `storeStateMachineCommitment` call (as `pallet-ismp` does via `store_latest_commitment_height`), and on veto of the latest height restore `_latestStateMachineHeight` to the tracked previous height rather than hardcoding `1`. Add a regression test asserting that after vetoing the latest commitment, only heights greater than the true prior height (not `1`) can be accepted by `update_client`.

### Proof of Concept
1. Consensus client verifies and stores heights `10`, then `100` for state machine `S` (`_latestStateMachineHeight[S] = 100`).
2. A fisherman submits a valid fraud proof and calls the handler path that invokes `deleteStateMachineCommitmentInternal(height=100, fisherman)`. Per the code, `_latestStateMachineHeight[S]` is reset to `1` (not `100`'s real predecessor, `10`, and certainly not blocking below-10 replays).
3. An attacker/relayer submits an `update_client` message containing a validly-signed but stale `StateCommitment` at height `50` (superseded long ago, or from a since-rotated validator set whose signature is still cryptographically valid for that height). Since `50 > 1`, the "only allow heights greater than latest" check in `update_client` passes [6](#0-5) , and this stale commitment is stored and becomes the new "latest" trusted state root for `S`.
4. Any request/response relying on storage proofs against state machine `S` can now be proven against this rolled-back, stale state root instead of the true current chain state, in violation of the guarantee that "state commitments must never let false remote state become trusted."

Note: I was not able to fully trace the exact end-to-end wiring from a public fisherman-facing entrypoint (e.g., `HandlerV2`/fraud-proof submission function) through to `deleteStateMachineCommitmentInternal` within the available index (only `EvmHost.sol`'s internal function and interface declarations were found); a Devin session with full repo access should confirm the exact caller and permission model of `deleteStateMachineCommitment` before treating this as fully confirmed.

### Citations

**File:** modules/ismp/core/src/handlers/consensus.rs (L53-61)
```rust
		let previous_latest_height = host.latest_commitment_height(id)?;
		let mut last_commitment_height = None;
		for commitment_height in commitment_heights.iter() {
			let state_height = StateMachineHeight { id, height: commitment_height.height };

			// Only allow heights greater than latest height
			if previous_latest_height > commitment_height.height {
				continue;
			}
```

**File:** evm/src/core/EvmHost.sol (L704-709)
```text
    function deleteStateMachineCommitment(StateMachineHeight memory height, address fisherman)
        external
        restrict(_hostParams.handler)
    {
        deleteStateMachineCommitmentInternal(height, fisherman);
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
