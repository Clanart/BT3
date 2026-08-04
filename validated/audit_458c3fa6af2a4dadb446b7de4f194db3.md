### Title
Destination-specific protocol fee override is stored under a different key than it is read on the Tron `IntentGatewayV2` — governance fee updates silently never apply, always charging the wrong (stale) fee - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The original report is about a hardcoded fee percentage that could not be changed post-deployment without adding an owner/governance-gated update function. Hyperbridge's `IntentGatewayV2` already implements exactly this pattern — a cross-chain-governance-gated `UpdateParams` request that lets Hyperbridge set a global `protocolFeeBps` plus per-destination fee overrides in `_destinationProtocolFees`. On the Tron variant of this contract, however, the write path and the read path for `_destinationProtocolFees` use **different, incompatible mapping keys**, so a destination-specific fee set by governance is written to a key that `placeOrder()` never looks up. The result is functionally identical to the original bug: the fee percentage effectively stays hardcoded at the global default for every destination, because the "update" mechanism silently fails to take effect.

### Finding Description
In `evm/tron/contracts/apps/IntentGatewayV2.sol`, the fee lookup in `placeOrder()` always hashes the destination chain identifier before reading the override: [1](#0-0) 

```
bytes32 destinationHash = keccak256(order.destination);
uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
if (protocolFeeBps == 0) {
    protocolFeeBps = _params.protocolFeeBps;
}
```

But the governance-driven write path in `onAccept()`'s `UpdateParams` handler stores the override keyed directly by the `stateMachineId` field from the incoming `DestinationFee` struct, with **no hashing**: [2](#0-1) 

```
} else if (kind == RequestKind.UpdateParams) {
    ParamsUpdate memory update = abi.decode(incoming.request.body[1:], (ParamsUpdate));
    ...
    for (uint256 i; i < update.destinationFees.length;) {
        bytes32 stateMachineId = update.destinationFees[i].stateMachineId;
        uint256 feeBps = update.destinationFees[i].destinationFeeBps;
        _destinationProtocolFees[stateMachineId] = feeBps;
        ...
```

Compare this to the sibling `NewDeployment` handler in the very same `onAccept()`, which *does* hash the analogous chain-identifier field before using it as a mapping key: [3](#0-2) 

```
if (kind == RequestKind.NewDeployment) {
    NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
    _instances[keccak256(body.stateMachineId)] = body.gateway;
```

and to the mainnet EVM sibling contract (`IntentsBase.sol`), whose `_updateParams` correctly hashes the destination `chain` bytes on write, matching the read side in `IntentGatewayV2.sol` (`evm/src/apps`): [4](#0-3) [5](#0-4) 

On the mainnet path, `_destinationProtocolFees[keccak256(chain)] = feeBps` (write) matches `_destinationProtocolFees[keccak256(order.destination)]` (read) — consistent. On the Tron path, the write key is the raw `stateMachineId` value supplied in the `DestinationFee` struct while the read key is `keccak256(order.destination)`. Unless the governance-supplied `stateMachineId` happens to equal `keccak256(order.destination)` byte-for-byte (which the struct's naming and the parallel `NewDeployment.stateMachineId` pattern — always raw, pre-hash, chain-id bytes — suggests it does not), the override is written under a key that `placeOrder()` will never query, and `protocolFeeBps` silently falls back to the global `_params.protocolFeeBps` for every order.

### Impact Explanation
This breaks the exact invariant the original bug report calls out: a fee percentage that governance believes it has changed for a specific destination remains effectively fixed at the old/global value, because the update mechanism doesn't wire into the value actually consumed during fee calculation. Every `placeOrder()` call to that destination charges the wrong protocol fee amount from ordinary users' escrowed inputs — either overcharging (if the intended per-destination fee was meant to be lower) or undercharging (protocol revenue loss, if it was meant to be higher). Because the fee is deducted before escrow and is never refunded, this is a direct, permanent mis-transfer of value on every affected order, not a cosmetic issue.

### Likelihood Explanation
This triggers deterministically and requires no attacker, malicious relayer, or compromised key — it fires on ordinary, permissionless `placeOrder()` calls the moment governance has legitimately set (or believes it has set) a destination-specific override via the standard `UpdateParams` cross-chain governance flow. The bug is structural (a field/key type mismatch baked into the contract), so it reproduces on 100% of orders to the affected destination for as long as the contract is deployed in this state.

### Recommendation
In the Tron `IntentGatewayV2.onAccept` `UpdateParams` branch, hash the destination-fee key the same way it is read in `placeOrder`, e.g. `_destinationProtocolFees[keccak256(update.destinationFees[i].stateMachineId)] = feeBps;` if `stateMachineId` is raw chain-id bytes, or otherwise change the read side to use the raw key consistently. Add a unit test asserting that a destination fee set via `onAccept(UpdateParams)` is actually applied by a subsequent `placeOrder()` to that same destination (mirroring the existing mainnet test `testPlaceOrderWithDestinationSpecificFee`), and add this exact test to the Tron test suite, since it currently appears absent.

### Proof of Concept
1. Governance dispatches an `UpdateParams` request (via Hyperbridge, `RequestKind.UpdateParams`) to the Tron `IntentGatewayV2`, including a `DestinationFee{ stateMachineId: X, destinationFeeBps: 50 }` intended to discount fees to 0.5% for destination `X`.
2. `onAccept` stores this at `_destinationProtocolFees[X] = 50` (raw key, no hash) — see lines 642-651.
3. A user calls `placeOrder(order)` with `order.destination = X` (the raw chain-id bytes, not pre-hashed).
4. `placeOrder` computes `destinationHash = keccak256(order.destination) = keccak256(X) ≠ X`, looks up `_destinationProtocolFees[keccak256(X)]`, finds it unset (0), and falls back to `_params.protocolFeeBps` (line 344-349) instead of the intended 50 bps override.
5. Every order to destination `X` is charged the stale/global fee rather than the governance-intended fee, indefinitely, with no revert or warning to indicate the override was ineffective.

### Citations

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L343-349)
```text
        uint256 inputsLen = order.inputs.length;
        // Use destination-specific protocol fee, fallback to source chain fee if zero
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L630-634)
```text
        if (kind == RequestKind.NewDeployment) {
            NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
            _instances[keccak256(body.stateMachineId)] = body.gateway;

            emit NewDeploymentAdded({stateMachineId: body.stateMachineId, gateway: body.gateway});
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L635-651)
```text
        } else if (kind == RequestKind.UpdateParams) {
            // Decode the body which includes optional destination-specific protocol fee updates
            ParamsUpdate memory update = abi.decode(incoming.request.body[1:], (ParamsUpdate));
            emit ParamsUpdated({previous: _params, current: update.params});
            _params = update.params;

            // Update destination-specific protocol fees if provided
            for (uint256 i; i < update.destinationFees.length;) {
                bytes32 stateMachineId = update.destinationFees[i].stateMachineId;
                uint256 feeBps = update.destinationFees[i].destinationFeeBps;
                _destinationProtocolFees[stateMachineId] = feeBps;

                unchecked {
                    ++i;
                }
                emit DestinationProtocolFeeUpdated(stateMachineId, feeBps);
            }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L557-567)
```text
        for (uint256 i; i < update.destinationFees.length;) {
            bytes memory chain = update.destinationFees[i].chain;
            uint256 feeBps = update.destinationFees[i].destinationFeeBps;
            if (feeBps >= 10_000) revert InvalidInput();
            _destinationProtocolFees[keccak256(chain)] = feeBps;

            unchecked {
                ++i;
            }
            emit DestinationProtocolFeeUpdated(string(chain), feeBps);
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L300-305)
```text
        // Phase 2: Compute protocol fees and commitment from actual received amounts.
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
```
