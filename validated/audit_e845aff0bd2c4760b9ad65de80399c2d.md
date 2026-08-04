Based on my research, I found a direct structural analog to the `setContracts()` bug: a duplicate/alternate code path that updates the same state (`_params`) as a validated setter, but skips the validation.

### Title
`IntentGatewayV2.onAccept` (Tron) applies governance `UpdateParams` without the invariant checks performed by the canonical implementation — ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The Tron fork of `IntentGatewayV2` implements the same `RequestKind.UpdateParams` cross-chain governance action as the canonical EVM `IntentGatewayV2`/`IntentsBase`, but the two implementations diverge: the canonical path validates every field of `Params` and every `destinationFeeBps` before committing, while the Tron path writes `_params` and `_destinationProtocolFees` directly with no checks at all — mirroring the reported pattern of one setter validating shared state while a parallel setter for the same state skips it.

### Finding Description
In the canonical implementation, params updates always go through `_validateParams` and per-entry bps checks: [1](#0-0) 

Specifically: `host`/`dispatcher` must be non-zero contracts, `surplusShareBps <= 10_000`, `protocolFeeBps < 10_000`, `priceOracle` must be a contract if non-zero, and each `destinationFeeBps < 10_000`.

In the Tron fork, the exact same governance action (`RequestKind.UpdateParams`, arriving via `onAccept`, gated only on `request.source == hyperbridge`) commits the new `Params` and destination fees with none of these checks: [2](#0-1) 

This is the same class of defect as `OrderMgr.setContracts()`: a second code path that mutates the same diamond/module state (`_params`, `_destinationProtocolFees`) that a sibling, validated function (`_validateParams` / `_updateParams` in the canonical contract) is supposed to guard. Because the Tron `onAccept` skips these invariants, a `Params` update that would be rejected on the canonical EVM deployment (e.g. `host == address(0)`, `dispatcher` pointing to an EOA/self-destructed contract, or `destinationFeeBps >= 10_000`) is silently accepted on Tron.

The corrupted values are `_params.host`, `_params.dispatcher`, and `_destinationProtocolFees[stateMachineId]`. Downstream, `placeOrder` computes `protocolFee = (originalAmount * protocolFeeBps) / 10_000` and `reducedAmount = originalAmount - protocolFee`: [3](#0-2) 
If `protocolFeeBps >= 10_000` is ever committed (blocked by validation on the canonical chain but not on Tron), this subtraction underflows/overtakes 100% of user input, either reverting every `placeOrder` call (bricking the gateway) or, depending on how `_destinationProtocolFees` interacts with `_params.protocolFeeBps` fallback logic, permitting fee extraction beyond intended bounds. Likewise a zero/invalid `host` or `dispatcher` written without validation breaks `host()`/`instance()` resolution used throughout `placeOrder`/`fillOrder`/`cancelOrder`, matching the "user orders no longer connected to the Market" failure mode described in the original report for `setOrderBooks`/`setContracts`.

### Impact Explanation
Existing guards (the `_validateParams`/bps-cap checks) exist specifically to prevent the gateway from being bricked or from producing broken fee arithmetic; because the Tron contract's `onAccept` handler for `UpdateParams` never calls an equivalent validator, any parameter set delivered through the standard, authenticated cross-chain governance flow can silently corrupt `_params`/`_destinationProtocolFees`, leading to gateway bricking, escrow/fee-accounting corruption, or arithmetic underflow in `placeOrder`. This falls under "logic attacks" / "false state acceptance" against production bridge state, since the same trusted message type is treated with different (weaker) invariants depending on which chain's contract processes it.

### Likelihood Explanation
The `onAccept` UpdateParams handler is a normal, expected part of the protocol's governance flow (not a hidden or rarely-used function), so any parameter update issued for the Tron deployment goes through this unguarded path every time; the divergence from the canonical validated implementation makes it easy for an update that is valid syntactically but semantically unsafe (e.g., a bps value copy-pasted incorrectly, or fields intended for a different chain) to be accepted with no on-chain safety net, unlike every other EVM deployment of the same contract.

### Recommendation
Add the same invariant checks used in `IntentsBase._validateParams`/`_updateParams` (host/dispatcher non-zero contract checks, `surplusShareBps <= 10_000`, `protocolFeeBps < 10_000`, `priceOracle` contract check, and per-entry `destinationFeeBps < 10_000`) to the Tron `IntentGatewayV2.onAccept` `RequestKind.UpdateParams` branch before committing `_params`/`_destinationProtocolFees`, so both deployments enforce identical safety invariants for the same cross-chain governance action.

### Proof of Concept
1. Hyperbridge governance (or any process constructing a valid ISMP `PostRequest` with `source == hyperbridge`) dispatches a `RequestKind.UpdateParams` body containing `ParamsUpdate.destinationFees[i].destinationFeeBps = 10000` (or higher) for the Tron `IntentGatewayV2`.
2. `onAccept` in `evm/tron/contracts/apps/IntentGatewayV2.sol` (lines 635–651) writes this value into `_destinationProtocolFees` with no bound check — the canonical `IntentsBase._updateParams` (lines 557–567) would have reverted with `InvalidInput`.
3. A subsequent `placeOrder` call to that destination computes `protocolFee = (originalAmount * 10000) / 10000 = originalAmount`, then `reducedAmount = originalAmount - protocolFee = 0`; for `protocolFeeBps > 10_000` the subtraction underflows, reverting all order placement to that destination and effectively bricking the corridor (or, if further arithmetic elsewhere tolerates it, extracting the full escrowed amount as "dust"). [4](#0-3)

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L532-567)
```text
    function _validateParams(Params memory p) internal view {
        if (p.host == address(0) || p.host.code.length == 0) revert InvalidInput();
        if (p.dispatcher == address(0) || p.dispatcher.code.length == 0) revert InvalidInput();
        if (p.surplusShareBps > 10_000) revert InvalidInput();
        if (p.protocolFeeBps >= 10_000) revert InvalidInput();
        if (p.priceOracle != address(0) && p.priceOracle.code.length == 0) revert InvalidInput();
    }

    /**
     * @dev Updates the gateway's configuration parameters and per-destination protocol fees.
     * Called by Hyperbridge governance to modify fee settings, host address, dispatcher,
     * price oracle, and other operational parameters.
     *
     * Validates all params before applying. Emits ParamsUpdated with the old and new params,
     * then iterates over any destination-specific fee overrides and applies them to
     * `_destinationProtocolFees`.
     *
     * @param update The parameter update containing new params and destination fee overrides.
     */
    function _updateParams(ParamsUpdate memory update) internal {
        _validateParams(update.params);

        emit ParamsUpdated({previous: _params, current: update.params});
        _params = update.params;

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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L342-358)
```text
        // Calculate reduced inputs (after protocol fees) for commitment and escrow
        uint256 inputsLen = order.inputs.length;
        // Use destination-specific protocol fee, fallback to source chain fee if zero
        bytes32 destinationHash = keccak256(order.destination);
        uint256 protocolFeeBps = _destinationProtocolFees[destinationHash];
        if (protocolFeeBps == 0) {
            protocolFeeBps = _params.protocolFeeBps;
        }
        TokenInfo[] memory reducedInputs;
        bytes32 commitment;

        if (protocolFeeBps > 0) {
            reducedInputs = new TokenInfo[](inputsLen);
            for (uint256 i; i < inputsLen;) {
                uint256 originalAmount = order.inputs[i].amount;
                uint256 protocolFee = (originalAmount * protocolFeeBps) / 10_000;
                uint256 reducedAmount = originalAmount - protocolFee;
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
