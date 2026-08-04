## Analysis

The Linea bug's core pattern: a **guard designed to validate "this isn't the first submission" instead blocks the legitimate first submission**, because the "empty/zero" sentinel used to detect "not yet initialized" is indistinguishable from "genuinely invalid." Hyperbridge's EVM host has a structurally identical guard in the state-machine-height bootstrap path.

### Title
First-ever state commitment for a newly onboarded state machine is silently dropped and never recoverable - ([File: evm/src/core/HandlerV2.sol])

### Summary
`HandlerV2.handleConsensus` only persists an `IntermediateState` reported by the consensus client if `latestStateMachineHeight(id) != 0`. But `_latestStateMachineHeight[id]` is *only* ever written by `storeStateMachineCommitment` (or by the one-shot admin `setConsensusState` bootstrap). For any state machine id that is onboarded organically through consensus-client-reported intermediate states (rather than through the admin's one-shot genesis call), its first legitimate update has `latestHeight == 0`, so the guard silently skips storage — and because that same guard is the only path that ever sets `_latestStateMachineHeight[id]` away from 0, every subsequent update for that id is skipped too, permanently.

### Finding Description
In `evm/src/core/HandlerV2.sol`: [1](#0-0) 

```solidity
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

`_latestStateMachineHeight[id]` is only ever set in two places:
1. `storeStateMachineCommitment`, handler-only, sets it as a side effect of a *successful* commitment write: [2](#0-1) 
2. `setConsensusState`, admin-only and one-shot (guarded by `_canReinitConsensus`), used for genesis bootstrap only: [3](#0-2) 

The public accessor's own doc comment confirms `0` is overloaded to mean "unsupported state machine": [4](#0-3) 

This is exactly the Linea pattern: the check `latestHeight != 0` is meant to prevent stale/out-of-order commitments from overwriting a valid latest height, but it uses `0` as the "no prior state" sentinel — which is indistinguishable from a legitimate, freshly-onboarded state machine whose true first height also arrives while `latestHeight == 0`. Just as Linea's `startingDataParentHash != EMPTY_HASH` masked out the correct first-batch check, here `latestHeight != 0` masks out the correct first-height store. Unlike Linea, this doesn't revert — it silently no-ops, and because the only way to ever set `_latestStateMachineHeight[id]` away from `0` is through this very check succeeding, the state machine is permanently stuck at height 0 for that id. No admin remediation function exists for post-genesis onboarding of a new state machine id other than `setConsensusState`, which is one-shot and consumed already for the primary bootstrap.

### Impact Explanation
Once a state machine id is trapped at height 0:
- `stateMachineCommitment(height)` never returns a valid commitment for that id, so `handlePostRequests`/response/timeout handlers relying on `StateCommitmentNotFound()` guard in `HandlerV2` will permanently revert for any message referencing that chain: [5](#0-4) 
- All cross-chain requests, responses, and timeouts destined for or sourced from that chain become permanently unprovable — funds/fees already escrowed or dispatched for that route (deposits, paymaster-funded fee payments, pending settlements) become unrecoverable because neither the success response nor the timeout can ever be proven against this host.
- This requires no malicious relayer, prover, or governance actor — it is a deterministic consequence of onboarding a new state machine id whose intermediate states arrive through the ordinary consensus-update path rather than through the one-shot admin bootstrap.

### Likelihood Explanation
This triggers on the ordinary, expected path of onboarding any additional state machine after initial deployment (e.g., adding support for a new counterparty chain post-launch) whenever that chain's first `IntermediateState` is reported through `IConsensusV2(...).verify(...)` rather than through a fresh `setConsensusState` call. Given `setConsensusState` is described as one-shot/genesis-only, any state machine added afterward is exposed. This is a deploy/operational-sequencing bug rather than one requiring an attacker, matching the "logic attack / false state (non-)acceptance leading to permanent fund lock" class the bounty targets.

### Recommendation
Distinguish "unsupported/never-configured state machine" from "configured but at height 0" using an explicit existence flag (e.g., a separate `mapping(uint256 => bool) supportedStateMachines`) rather than overloading `_latestStateMachineHeight == 0` as the sentinel, mirroring the Linea fix's approach of gating on an explicit "is this the first submission" condition instead of comparing against a value that is legitimately empty on first use.

### Proof of Concept
1. Deploy `EvmHost`/`HandlerV2`; do not call `setConsensusState` for state machine id `X` (only the primary counterparty is bootstrapped at genesis).
2. Relayer submits a valid consensus proof via `handleConsensus` whose `IConsensusV2.verify` returns an `IntermediateState` for the new id `X` at height `H1`.
3. `host.latestStateMachineHeight(X)` returns `0` → guard `latestHeight != 0` is false → `storeStateMachineCommitment` is never called for `X`.
4. `_latestStateMachineHeight[X]` remains `0` forever; every future consensus update reporting intermediate states for `X` repeats step 3.
5. Any `handlePostRequests`/`handleGetResponses`/timeout call referencing a `StateMachineHeight` for `X` reverts with `StateCommitmentNotFound()` indefinitely, permanently stranding any funds/messages routed through chain `X`.

### Citations

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

**File:** evm/src/core/HandlerV2.sol (L199-202)
```text
        bytes32 root = host.stateMachineCommitment(request.proof.height).overlayRoot;
        if (root == bytes32(0)) revert StateCommitmentNotFound();
        bool valid = MerkleMountainRange.VerifyProof(root, request.proof.multiproof, leaves, request.proof.leafCount);
        if (!valid) revert InvalidProof();
```

**File:** evm/src/core/EvmHost.sol (L505-510)
```text
    /**
     * @return the latest state machine height for the given stateMachineId. If it returns 0, the state machine is unsupported.
     */
    function latestStateMachineHeight(uint256 id) external view returns (uint256) {
        return _latestStateMachineHeight[id];
    }
```

**File:** evm/src/core/EvmHost.sol (L687-699)
```text
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

**File:** evm/src/core/EvmHost.sol (L776-788)
```text
    function setConsensusState(bytes memory state, StateMachineHeight memory height, StateCommitment memory commitment)
        public
        restrict(_hostParams.admin)
    {
        if (!_canReinitConsensus()) revert UnauthorizedAction();

        _consensusState = state;
        _consensusUpdateTimestamp = block.timestamp;

        _stateCommitments[height.stateMachineId][height.height] = commitment;
        _stateCommitmentsUpdateTime[height.stateMachineId][height.height] = block.timestamp;
        _latestStateMachineHeight[height.stateMachineId] = height.height;
    }
```
