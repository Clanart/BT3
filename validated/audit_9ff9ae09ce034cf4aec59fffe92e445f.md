## Finding

### Title
`handleConsensus` skips storing new state-machine commitments and refreshing the consensus update timestamp whenever the verified consensus state is byte-identical to the previous one - (`evm/src/core/HandlerV2.sol`)

### Summary
The `harvest()` bug in the seed report is caused by a side-effect (`lastHarvest = block.timestamp`) that is gated behind a condition and never executed on the intended path, silently defeating the invariant the variable is supposed to protect. `HandlerV2.handleConsensus` in this repository has the same shape: an early `return` short-circuits *before* the state-machine commitments are stored and before the host's consensus-update timestamp is refreshed, whenever the freshly-verified consensus state equals the previously stored one.

### Finding Description
`handleConsensus` reads the delay since the last update, verifies a new consensus proof, and then does: [1](#0-0) 

```solidity
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
        ...
        host.storeStateMachineCommitment(stateMachineHeight, intermediate.commitment);
        ...
    }
    ...
}
```

The consensus "state" tracked here is typically the validator/authority set information, which only changes at epoch/session boundaries. Between rotations, a legitimate relayer submitting a fresh, valid consensus proof for a new finalized height will have `previousState == verifiedState` (no authority-set change) even though new intermediate states (i.e. new finalized parachain/relay heights carrying state-machine commitments) were just verified.

Because the equality check triggers an unconditional early `return`, on this — the common — path:
1. `host.storeConsensusState` is never called, so the timestamp used by `consensusUpdateTime()` (`_consensusUpdateTimestamp`, exposed at `evm/src/core/EvmHost.sol:420-422`) is never refreshed. [2](#0-1) 
2. None of the newly verified `intermediates` are written via `host.storeStateMachineCommitment`, so state-machine heights for connected chains stop advancing.

This mirrors the seed bug exactly: `lastHarvest`/`_consensusUpdateTimestamp` is only updated on a conditional branch of the function, and the routine execution path for the protocol's steady-state operation (repeated legitimate calls without a "real" state transition) systematically skips it.

### Impact Explanation
`consensusUpdateTime()` gates the unrecoverable expiry check at the top of the very same function:

```solidity
uint256 delay = block.timestamp - host.consensusUpdateTime();
if (delay >= host.unStakingPeriod()) revert ConsensusClientExpired();
```

If the timestamp is frozen at whatever value it had at the last *actual* authority-set rotation (which can be far in the past, or even the deployment/genesis value if no rotation has happened yet), then `delay` keeps growing indefinitely with wall-clock time regardless of how many valid consensus proofs are being submitted. Once `delay >= unStakingPeriod`, every future `handleConsensus` call reverts with `ConsensusClientExpired()` — a state the contract's own comment calls "unrecoverable": [3](#0-2) 

This permanently bricks the consensus client on that EVM host, even though relaying has been happening continuously and correctly. Beyond the DoS, the more direct consequence is that `storeStateMachineCommitment` for intermediate states is also skipped on the same path, so the host's view of counterparty chain state silently stops advancing (new heights/commitments needed for request/response/timeout proof verification, and for the challenge-period logic used by `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, `handleGetRequestTimeouts` in the same file) never gets updated after the point where verified-state first becomes byte-identical to the stored one. Because this is the state used to prove/settle in-flight requests, responses, and timeouts, this can result in messages/assets being unable to be finalized (funds effectively stuck in escrow on one side of the bridge) once the freeze/staleness condition is hit — a logic attack on the core proof/settlement pipeline, not a generic gas/network DoS.

### Likelihood Explanation
No malicious relayer, prover, or admin is required. Any relayer submitting a syntactically and cryptographically valid consensus proof for a period without an authority-set rotation will trigger `previousState == verifiedState`, which is the ordinary/expected steady-state case for most consensus-update cadences (validator-set rotations occur far less often than proof submissions). This makes the condition trivially and repeatedly reachable through the normal, permissionless `handleConsensus` entrypoint, with no attacker-controlled input needed beyond a legitimate proof.

### Recommendation
Move the update of `_consensusUpdateTimestamp` (and the storage of intermediate state-machine commitments) outside of the "state changed" branch, so they execute on every successful proof verification regardless of whether `verifiedState` differs from `previousState`:

```solidity
function handleConsensus(IHost host, bytes calldata proof) external notFrozen(host) {
    uint256 delay = block.timestamp - host.consensusUpdateTime();
    if (delay >= host.unStakingPeriod()) revert ConsensusClientExpired();

    bytes memory previousState = host.consensusState();
    (bytes memory verifiedState, IntermediateState[] memory intermediates, uint256 nextAuthoritySetId) =
        IConsensusV2(host.consensusClient()).verify(previousState, proof);

    // Always record that a fresh, valid proof was processed.
    host.storeConsensusUpdateTime(block.timestamp);

    // Always persist newly verified intermediate state-machine commitments.
    uint256 intermediatesLen = intermediates.length;
    for (uint256 i = 0; i < intermediatesLen; i++) {
        ...
        host.storeStateMachineCommitment(stateMachineHeight, intermediate.commitment);
    }

    if (keccak256(previousState) != keccak256(verifiedState)) {
        host.storeConsensusState(verifiedState);
    }
    ...
}
```
(Introduce a dedicated `storeConsensusUpdateTime` host setter if one does not already exist independent of `storeConsensusState`.)

### Proof of Concept
1. Deploy an `EvmHost`/`HandlerV2` pair with a real `unStakingPeriod` (e.g. 14 days) and a consensus client whose "state" encodes only the current authority/validator set.
2. Submit an initial valid consensus proof; `previousState` differs from the genesis-configured state, so `storeConsensusState` runs and `_consensusUpdateTimestamp` is set to `T0`.
3. Continue submitting valid consensus proofs for subsequent finalized heights that do **not** cross an authority-set rotation boundary. For each of these, `keccak256(previousState) == keccak256(verifiedState)` is true, so the function returns early: `_consensusUpdateTimestamp` stays at `T0` and no new `storeStateMachineCommitment` calls occur.
4. Once `block.timestamp - T0 >= unStakingPeriod`, call `handleConsensus` again with another valid proof. It now reverts with `ConsensusClientExpired()` — permanently, per the contract's own documented behavior — even though proofs have been submitted continuously and correctly the entire time, and even though no actual authority-set staleness has occurred (an authority-set rotation could have happened just before this call and it would make no difference, since the timestamp was never refreshed).

### Citations

**File:** evm/src/core/HandlerV2.sol (L66-69)
```text

    // The consensus client has now expired to mitigate
    // long fork attacks, this is unrecoverable.
    error ConsensusClientExpired();
```

**File:** evm/src/core/HandlerV2.sol (L144-164)
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
```

**File:** evm/src/core/EvmHost.sol (L417-422)
```text
    /**
     * @return the last updated time of the consensus client
     */
    function consensusUpdateTime() external view returns (uint256) {
        return _consensusUpdateTimestamp;
    }
```
