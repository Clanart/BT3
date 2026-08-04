### Title
`onAccept(UpdateParams)` in the Tron IntentGatewayV2 applies `protocolFeeBps`/`destinationFeeBps` with no upper-bound validation, unlike the canonical implementation - ([File: evm/tron/contracts/apps/IntentGatewayV2.sol])

### Summary
The canonical `IntentGatewayV2`/`IntentsBase` implementation validates every governance-supplied parameter before applying it: `_validateParams` rejects `protocolFeeBps >= 10_000` and `_updateParams` rejects any `destinationFeeBps >= 10_000` before writing to `_destinationProtocolFees`. The Tron variant of the same contract implements the identical `UpdateParams`/`onAccept` flow but drops all of this validation, directly assigning `_params = update.params` and `_destinationProtocolFees[stateMachineId] = feeBps` with no bounds check whatsoever.

### Finding Description
In the reference implementation, `_updateParams` and `_validateParams` gate every write to protocol-fee-related state: [1](#0-0) 

In `evm/tron/contracts/apps/IntentGatewayV2.sol`, the equivalent code path (`onAccept`, `RequestKind.UpdateParams`) performs the decode-and-assign with **no** call to any validation routine: [2](#0-1) 

This means `_params.protocolFeeBps` and any entry in `_destinationProtocolFees` can be set to `>= 10_000` (i.e. ≥100%) or to any other unconstrained value on the Tron deployment, whereas the mainline EVM/base contracts enforce `< 10_000` for exactly these fields (confirmed both by `_validateParams`/`_updateParams` and by the dedicated Foundry tests `testRevert_SetParams_ProtocolFeeBpsTooHigh` and `testRevert_UpdateParams_DestinationFeeBpsTooHigh`): [3](#0-2) 

The corrupted value flows directly into `placeOrder`'s fee-reduction arithmetic, which assumes `protocolFeeBps < 10_000`: [4](#0-3) 

With `protocolFeeBps >= 10_000`, `protocolFee = originalAmount * protocolFeeBps / 10_000` becomes `>= originalAmount`, so `reducedAmount = originalAmount - protocolFee` underflows and reverts under Solidity 0.8 checked arithmetic — permanently bricking `placeOrder` for every user targeting that destination (or for the whole gateway if `_params.protocolFeeBps` itself is corrupted, since that is the fallback used whenever no destination-specific override exists). With `protocolFeeBps` set to exactly `10_000`, every order's escrowed/reduced amount collapses to `0` while the user is still charged full `order.fees`, permanently and silently zeroing out the recorded escrow ledger entry (`_orders[commitment][token] = 0`) for future redemption/refund bookkeeping.

### Impact Explanation
Because `_params`/`_destinationProtocolFees` are core state consulted on every `placeOrder` call, an out-of-range fee value corrupts either (a) the availability of order placement (permanent revert/DoS on the cross-chain intents flow for a destination), or (b) the integrity of escrow accounting (silently reduces every user's escrowed amount to zero while `order.fees` are still collected), i.e. a genuine loss-of-funds/accounting-integrity condition, not merely a cosmetic issue. This is exactly the same bug class as the seed report (`collateralRatio_`/`weeklyPremium_` missing bounds checks in `Pool.sol`) — an unchecked basis-points/ratio parameter that downstream arithmetic implicitly assumes is bounded.

### Likelihood Explanation
The write path is reachable only via the authenticated `onAccept` flow gated to `incoming.request.source == hyperbridge`, matching the same trust model as the canonical contract's governance-only `UpdateParams` path. The vulnerability is not that an attacker can forge the governance message — it is that the Tron contract itself omits the safety validation the rest of the codebase treats as mandatory, so a legitimate (non-malicious) governance update that would be rejected on every other deployment silently corrupts state and breaks fund accounting on Tron. This is a direct code-parity/regression bug, provable purely from the two source files without any additional assumptions about relayer or admin misbehavior.

### Recommendation
Port `_validateParams`/`_updateParams`'s bounds checks (`protocolFeeBps < 10_000`, `surplusShareBps <= 10_000`, and per-destination `destinationFeeBps < 10_000`) into `evm/tron/contracts/apps/IntentGatewayV2.sol`'s `onAccept(RequestKind.UpdateParams)` handler before `_params` and `_destinationProtocolFees` are written, so the Tron deployment enforces the same invariants as the canonical implementation.

### Proof of Concept
1. Hyperbridge governance (the only authorized `onAccept` caller for this action) dispatches an `UpdateParams` request to the Tron `IntentGatewayV2` with `update.destinationFees = [{stateMachineId: X, destinationFeeBps: 10_000}]` (or `update.params.protocolFeeBps = 10_000`).
2. `onAccept` decodes the body and writes `_destinationProtocolFees[X] = 10_000` unconditionally (evm/tron/contracts/apps/IntentGatewayV2.sol:642-651) — no revert occurs, unlike the canonical contract which would revert with `InvalidInput` (evm/src/apps/intentsv2/IntentsBase.sol:560).
3. Any user calls `placeOrder` with `order.destination == X`: `protocolFeeBps` resolves to `10_000`, `protocolFee == originalAmount`, `reducedAmount = 0` for every input token — the user's tokens are still escrowed/pulled per the original amounts elsewhere in the flow, but the recorded `_orders[commitment][token]` escrow ledger is `0`, breaking later redemption/refund bookkeeping for that order.
4. If `destinationFeeBps` is instead set `> 10_000`, step 3 reverts with an arithmetic underflow, permanently blocking `placeOrder` for destination `X`.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L532-568)
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
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L344-358)
```text
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

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L3595-3657)
```text
    /// @notice setParams rejects protocolFeeBps >= 10000.
    function testRevert_SetParams_ProtocolFeeBpsTooHigh() public {
        IntentGatewayV2 gw = _deployGatewayProxy();
        Params memory p = Params({
            host: address(host),
            dispatcher: address(dispatcher),
            solverSelection: false,
            surplusShareBps: 5000,
            protocolFeeBps: 10000,
            priceOracle: address(0)
        });
        vm.expectRevert(IntentsBase.InvalidInput.selector);
        gw.initialize(p, new bytes[](0));
    }

    /// @notice setParams rejects non-contract priceOracle.
    function testRevert_SetParams_EOAPriceOracle() public {
        IntentGatewayV2 gw = _deployGatewayProxy();
        Params memory p = Params({
            host: address(host),
            dispatcher: address(dispatcher),
            solverSelection: false,
            surplusShareBps: 5000,
            protocolFeeBps: 0,
            priceOracle: address(0xbeef)
        });
        vm.expectRevert(IntentsBase.InvalidInput.selector);
        gw.initialize(p, new bytes[](0));
    }

    /// @notice updateParams via governance rejects destinationFeeBps >= 10000.
    function testRevert_UpdateParams_DestinationFeeBpsTooHigh() public {
        DestinationFee[] memory fees = new DestinationFee[](1);
        fees[0] = DestinationFee({destinationFeeBps: 10000, chain: bytes("ARBITRUM")});

        ParamsUpdate memory update = ParamsUpdate({
            params: Params({
                host: address(host),
                dispatcher: address(dispatcher),
                solverSelection: false,
                surplusShareBps: 5000,
                protocolFeeBps: 0,
                priceOracle: address(0)
            }),
            destinationFees: fees
        });

        bytes memory body = bytes.concat(bytes1(uint8(IntentsBase.RequestKind.UpdateParams)), abi.encode(update));

        PostRequest memory request = PostRequest({
            source: host.hyperbridge(),
            dest: host.host(),
            nonce: 0,
            from: abi.encodePacked(address(intentGateway)),
            to: abi.encodePacked(address(intentGateway)),
            body: body,
            timeoutTimestamp: 0
        });

        vm.prank(address(host));
        vm.expectRevert(IntentsBase.InvalidInput.selector);
        intentGateway.onAccept(IncomingPostRequest({relayer: address(0), request: request}));
    }
```
