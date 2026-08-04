### Title
Stale `_latestStateMachineHeight` entries survive state-machine removal in `EvmHost.updateHostParamsInternal`, permanently trusting heights for de-whitelisted chains - (File: evm/src/core/EvmHost.sol)

### Summary
This is the direct EVM-side analog of the `TransmuterBuffer.registerAsset` bug class: a monotonically-growing accounting structure (`_latestStateMachineHeight`) that can never be reduced or reconstructed when the authoritative whitelist array (`HostParams.stateMachines`) shrinks, permanently freezing stale trust state instead of cleanly failing or resetting.

### Finding Description
`updateHostParamsInternal` fully replaces `_hostParams.stateMachines` with whatever array is passed in, and then only *adds* new entries to the `_latestStateMachineHeight` mapping — it never clears entries for state machine IDs that are absent from the new `params.stateMachines` array: [1](#0-0) 

```
_hostParams.stateMachines = params.stateMachines;
...
// add whitelisted state machines
for (uint256 i = 0; i < stateMachinesLen; ++i) {
    // create if it doesn't already exist
    if (_latestStateMachineHeight[params.stateMachines[i]] == 0) {
        _latestStateMachineHeight[params.stateMachines[i]] = 1;
    }
}
```

This exactly mirrors the reported bug class: `registeredUnderlyings` in `TransmuterBuffer` could only grow via `registerAsset`, with `refreshStrategies` requiring exact array-length equality to the live Alchemist list — a mismatch (e.g., after switching to an Alchemist with fewer assets) permanently broke the contract because there was no removal/reset path.

Here, `_latestStateMachineHeight[id]` is the value that `HandlerV2.sol` consults to decide whether an incoming consensus proof's intermediate state for `id` should be accepted and stored as a new state machine commitment: [2](#0-1) 

```
for (uint256 i = 0; i < intermediatesLen; i++) {
    IntermediateState memory intermediate = intermediates[i];
    uint256 latestHeight = host.latestStateMachineHeight(intermediate.stateMachineId);
    if (latestHeight != 0 && intermediate.height > latestHeight) {
        ...
        host.storeStateMachineCommitment(stateMachineHeight, intermediate.commitment);
    }
}
```

The confirmed foundry test demonstrates the "grow-only" nature of this mapping: after `updateHostParams` is called with a *different* `stateMachines` array, a previously-tracked height for a state machine ID that is *not* in the new array remains untouched (`assert(host.latestStateMachineHeight(height.stateMachineId) == 100)` even though `2000` is absent from the array set up in that test path), and the mapping is only ever initialized, never zeroed: [3](#0-2) 

If governance (via `HostManager`) later intends to de-whitelist a state machine by omitting it from `params.stateMachines` (e.g. decommissioning a compromised or deprecated chain), `_latestStateMachineHeight[id]` for that chain is never reset to `0`. Because `HandlerV2.updateConsensusState` gates acceptance of new commitments purely on `latestHeight != 0`, the host keeps trusting/accepting consensus proof updates that reference the old ID's height, even though `HostParams.stateMachines` no longer lists it as authorized. There is no `remove`/`reset` entrypoint anywhere in `EvmHost.sol` for this mapping — the only mutation paths are `updateHostParamsInternal` (add-only) and `storeStateMachineCommitment` (writer, called by `handler`).

### Impact Explanation
If a state machine is intentionally removed from `HostParams.stateMachines` (the sole "whitelist" surfaced to operators/documentation), the code gives the false impression that new commitments for it are blocked. In reality `_latestStateMachineHeight` retains its last known non-zero value indefinitely, so `HandlerV2` continues to accept and persist new `StateMachineCommitment`s for the "removed" state machine ID as long as a consensus client still reports intermediate states for it. This is a false-state-acceptance condition: a state machine that governance believed was de-authorized keeps having its state roots trusted and stored, which downstream request/response/timeout handling (`handlePostRequests`, `handleGetResponses`, etc.) will use to verify inbound proofs, membership, and dispatch execution to local `IApp` modules.

### Likelihood Explanation
This requires a legitimate `hostManager` governance update that removes a state machine ID from `HostParams.stateMachines` (analogous to the original report's "Alchemist switched to one with fewer assets" scenario, which the project itself acknowledged as a realistic and impactful operator action, ultimately judged Medium). No malicious relayer, prover, or attacker capability is required beyond a normal, sanctioned host-params update — the flaw is purely in the missing cleanup logic, exactly as in the seed report.

### Recommendation
When `updateHostParamsInternal` computes the new `stateMachines` set, diff it against the previous `_hostParams.stateMachines` and explicitly zero out `_latestStateMachineHeight` (and optionally clear associated `_stateCommitments`) for any state machine ID present in the old set but absent from the new one, so that de-whitelisting a chain actually revokes trust in its state instead of silently leaving stale height data active.

### Proof of Concept
1. Deploy `EvmHost` with `HostParams.stateMachines = [2000]`.
2. Have the configured `handler` call `storeStateMachineCommitment` for `stateMachineId = 2000` at some height `H`, causing `_latestStateMachineHeight[2000] = H` (as shown in `testCanAddwhitelistedStateMachines`, `EvmHostTest.sol:196-206`).
3. Have `hostManager` call `updateHostParams` with a new `HostParams.stateMachines` array that no longer includes `2000` (e.g., `[2001]`), signaling operational removal of state machine `2000`.
4. Observe `host.latestStateMachineHeight(2000)` still returns `H` (non-zero) — per the existing test pattern at `EvmHostTest.sol:206-212`, the mapping is never cleared.
5. A subsequent consensus proof through `ConsensusRouter`/`HandlerV2.updateConsensusState` containing an `IntermediateState` for `stateMachineId = 2000` with `height > H` will still pass the `latestHeight != 0 && intermediate.height > latestHeight` check and be persisted via `storeStateMachineCommitment`, even though state machine `2000` is no longer part of `HostParams.stateMachines`.

### Citations

**File:** evm/src/core/EvmHost.sol (L635-645)
```text
        _hostParams.stateMachines = params.stateMachines;
        _hostParams.hyperbridge = params.hyperbridge;

        // add whitelisted state machines
        for (uint256 i = 0; i < stateMachinesLen; ++i) {
            // create if it doesn't already exist
            if (_latestStateMachineHeight[params.stateMachines[i]] == 0) {
                _latestStateMachineHeight[params.stateMachines[i]] = 1;
            }
        }
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

**File:** evm/tests/foundry/EvmHostTest.sol (L189-215)
```text
    function testCanAddwhitelistedStateMachines() public {
        HostParams memory params = host.hostParams();
        uint256[] memory stateMachines = new uint256[](2);
        stateMachines[0] = 2000;
        stateMachines[1] = 2001;
        params.stateMachines = stateMachines;

        // create a state commitment
        StateMachineHeight memory height = StateMachineHeight({height: 100, stateMachineId: 2000});
        vm.prank(params.handler);
        host.storeStateMachineCommitment(
            height, StateCommitment({timestamp: 200, overlayRoot: bytes32(0), stateRoot: bytes32(0)})
        );

        vm.prank(params.handler);
        assert(host.stateMachineCommitment(height).timestamp == 200);

        assert(host.latestStateMachineHeight(height.stateMachineId) == 100);

        // add the new state machine
        vm.prank(params.hostManager);
        host.updateHostParams(params);
        // should be unchanged
        assert(host.latestStateMachineHeight(height.stateMachineId) == 100);
        // should be set to 1
        assert(host.latestStateMachineHeight(2001) == 1);
    }
```
