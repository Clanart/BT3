## Analog Found: Veto reset to a fixed sentinel height allows stale/superseded state commitments to be re-accepted as "latest" on EVM hosts

### Title
Fisherman veto resets `_latestStateMachineHeight` to a hardcoded `1` instead of the last known-good height, letting a stale/lower state commitment be re-accepted as canonical - (File: `evm/src/core/EvmHost.sol`)

### Summary
The external report's core broken invariant is: *when time-sensitive/security-critical data is invalidated (a revoked cert / CRL), the system must not let stale data silently remain usable, and no compensating check re-validates freshness at the consumption site.* The local analog is in Hyperbridge's EVM state-machine veto path: `EvmHost.deleteStateMachineCommitmentInternal` invalidates a fraudulent `StateCommitment` but resets the bookkeeping value `_latestStateMachineHeight` to a hardcoded `1` rather than to the actual last-known-good height (as the Substrate pallet does via `PreviousStateMachineHeight`). Combined with `HandlerV2.handleConsensus`'s unconditional overwrite semantics, this allows a subsequent (even previously valid/old) consensus proof to reinstate an older, already-superseded state commitment as the new "latest," undermining the fisherman veto/challenge-period protection that consensus proofs rely on to reject byzantine/fraudulent state.

### Finding Description
When a fisherman detects a fraudulent `StateCommitment` still within its challenge period, it calls `veto_state_commitment`, which on EVM routes to `EvmHost.deleteStateMachineCommitment` → `deleteStateMachineCommitmentInternal`: [1](#0-0) 

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
```

Unlike the Substrate implementation, which restores the tracked `PreviousStateMachineHeight` on veto ( [2](#0-1) ), the EVM host discards the actual last-good height entirely and substitutes the constant `1`.

This corrupted `_latestStateMachineHeight` value is then consumed, unguarded, by `HandlerV2.handleConsensus`: [3](#0-2) 

```solidity
function handleConsensus(IHost host, bytes calldata proof) external notFrozen(host) {
    ...
    for (uint256 i = 0; i < intermediatesLen; i++) {
        IntermediateState memory intermediate = intermediates[i];
        uint256 latestHeight = host.latestStateMachineHeight(intermediate.stateMachineId);
        if (latestHeight != 0 && intermediate.height > latestHeight) {
            StateMachineHeight memory stateMachineHeight =
                StateMachineHeight({stateMachineId: intermediate.stateMachineId, height: intermediate.height});
            host.storeStateMachineCommitment(stateMachineHeight, intermediate.commitment);
        }
    }
    ...
}
```

And `storeStateMachineCommitment` writes the height unconditionally, with no monotonicity re-check against any other stored height: [4](#0-3) 

```solidity
function storeStateMachineCommitment(StateMachineHeight memory height, StateCommitment memory commitment)
    external
    restrict(_hostParams.handler)
{
    _stateCommitments[height.stateMachineId][height.height] = commitment;
    _stateCommitmentsUpdateTime[height.stateMachineId][height.height] = block.timestamp;
    _latestStateMachineHeight[height.stateMachineId] = height.height;
    ...
}
```

Note also that unlike the Substrate `update_client` handler, which explicitly sorts intermediate states ascending and skips heights already committed (`if host.state_machine_commitment(state_height).is_ok() { continue; }`) ( [5](#0-4) ), the EVM `handleConsensus` performs neither sort nor duplicate/ordering safety check — it trusts `latestStateMachineHeight` alone as the sole gate against regressions.

The exact corrupted value is `_latestStateMachineHeight[stateMachineId]`: after a veto of the current latest height H, it becomes `1` instead of the true prior safe height P (where 1 < P < H). Any subsequent `handleConsensus` call whose consensus proof includes an intermediate state at any height `h` with `1 < h < H` (including a height that had already been superseded/committed earlier with a *different*, correct commitment, or a stale proof for an older, already-obsolete finalized height that is still a cryptographically valid consensus proof) will pass the `intermediate.height > latestHeight` check and get written via `storeStateMachineCommitment`, overwriting `_latestStateMachineHeight` down to `h`. This directly defeats the purpose of the veto/challenge-period mechanism: the fisherman's decision to reject height H's fraudulent state can be circumvented by resurrecting an arbitrary intermediate height as "latest," and — because `storeStateMachineCommitment` has no protection against overwriting an already-stored, different commitment at the same height — a state height that was previously finalized with a correct, verified commitment could later be silently overwritten if a different (stale) consensus proof for that same height is resubmitted through this path.

### Impact Explanation
This breaks the "false remote state must never become trusted" invariant central to Hyperbridge's design. Requests, responses, and timeouts are all authorized based on `stateMachineCommitment(height)` and its associated `stateMachineCommitmentUpdateTime`, gated only by challenge period (see `HandlerV2.handlePostRequests`/`handleGetResponses`/`handlePostRequestTimeouts`) ( [6](#0-5) ). If the bookkeeping height can be rolled back and a stale/overwritten commitment can re-enter as "latest," an attacker/relayer can potentially cause the host to accept state proofs anchored to state that Hyperbridge's own fisherman apparatus explicitly rejected, or to state that has already been correctly superseded — leading to false proof/state acceptance, and downstream unauthorized dispatch of requests/responses that should never have been considered valid.

### Likelihood Explanation
The path is reachable by any permissionless relayer calling the public `handleConsensus` entrypoint with a valid consensus proof (no admin/governance/relayer-trust assumption is required beyond the normal trust already placed in the consensus client's cryptographic verification) after a legitimate fisherman veto has occurred. It does not require a malicious peer, prover, or leaked key — only that (a) a veto happens (a normal, expected operational event per the fishermen design docs) and (b) a subsequently submitted, otherwise-valid consensus proof contains an intermediate state at a height between `1` and the vetoed height.

### Recommendation
- Short term: On EVM, track the true prior safe height (mirroring `PreviousStateMachineHeight` in the Substrate pallet) and reset `_latestStateMachineHeight` to that value on veto instead of the hardcoded `1`. Additionally, add a duplicate/overwrite guard in `storeStateMachineCommitment` (or in `handleConsensus`) so an already-stored commitment at a given height cannot be silently overwritten by a different value.
- Long term: Align EVM and Substrate consensus-update logic (sorting intermediate states, skipping already-committed heights) so both implementations enforce the same monotonicity and non-overwrite guarantees after a veto.

### Proof of Concept
1. Consensus client verifies and stores state commitments at heights 10, 20, 30 for a given `stateMachineId` (via successive `handleConsensus` calls); `_latestStateMachineHeight = 30`.
2. A fisherman detects height 30's commitment is fraudulent and calls `veto_state_commitment` (Substrate) which invokes `EvmHost.deleteStateMachineCommitment(height=30, fisherman)`. Since `_latestStateMachineHeight[id] == 30`, it is reset to `1` per `deleteStateMachineCommitmentInternal` (`evm/src/core/EvmHost.sol:718-721`).
3. Height 20's commitment is still correctly stored and untouched, but `_latestStateMachineHeight[id]` no longer reflects it (it now reads `1`).
4. A relayer submits any valid consensus proof (potentially historical/already-processed) whose intermediate states include height 25 (a height that never should have been reconsidered as "next," or one carrying a manipulated/stale commitment). `HandlerV2.handleConsensus` checks `latestHeight (1) != 0 && 25 > 1` → true, and calls `host.storeStateMachineCommitment(height=25, commitment)`, which unconditionally sets `_latestStateMachineHeight[id] = 25`.
5. The protocol's "latest" state pointer has now regressed/been overwritten outside of the veto's intended scope, and any state height between 1 and 30 can be freely (re)written as "latest," bypassing the safety the veto was meant to enforce.

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

**File:** evm/src/core/EvmHost.sol (L711-732)
```text
    /**
     * @dev Delete the state commitment at given state height.
     */
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

**File:** modules/pallets/ismp/src/host.rs (L194-220)
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

**File:** evm/src/core/HandlerV2.sol (L181-211)
```text
    function handlePostRequests(IHost host, PostRequestMessage calldata request) external notFrozen(host) {
        uint256 timestamp = block.timestamp;
        uint256 delay = timestamp - host.stateMachineCommitmentUpdateTime(request.proof.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        uint256 requestsLen = request.requests.length;
        MerkleMountainRange.Leaf[] memory leaves = new MerkleMountainRange.Leaf[](requestsLen);

        for (uint256 i = 0; i < requestsLen; ++i) {
            PostRequestLeaf memory leaf = request.requests[i];
            // check destination
            if (!leaf.request.dest.equals(host.host())) revert InvalidMessageDestination();
            // check time-out
            if (timestamp >= leaf.request.timeout()) revert MessageTimedOut();
            leaves[i] = MerkleMountainRange.Leaf(leaf.index, leaf.request.hash());
        }

        bytes32 root = host.stateMachineCommitment(request.proof.height).overlayRoot;
        if (root == bytes32(0)) revert StateCommitmentNotFound();
        bool valid = MerkleMountainRange.VerifyProof(root, request.proof.multiproof, leaves, request.proof.leafCount);
        if (!valid) revert InvalidProof();

        for (uint256 i = 0; i < requestsLen; ++i) {
            PostRequestLeaf memory leaf = request.requests[i];
            // duplicate request?
            if (host.requestReceipts(leaf.request.hash()) != address(0)) revert DuplicateMessage();
            host.dispatchIncoming(leaf.request, _msgSender());
        }
    }

```

**File:** modules/ismp/core/src/handlers/consensus.rs (L51-70)
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
```
