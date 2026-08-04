## Finding

### Title
`placeOrder` escrows user funds without validating that `order.destination` has a registered `IntentGatewayV2` instance, permanently locking escrow when no such instance exists - (File: `evm/src/apps/IntentGatewayV2.sol`)

### Summary
`IntentGatewayV2.placeOrder` unconditionally escrows the caller's input tokens for a cross-chain order regardless of whether the specified `order.destination` state machine actually has a registered `IntentGatewayV2` deployment. Every recovery path — same-chain cancel, source-side cross-chain cancel, and destination-side cross-chain cancel — either does not apply or hard-reverts when the destination has no registered instance, leaving the escrowed tokens permanently stuck with no code path to reclaim them. This is the same broken invariant as the Flayer H-8 report: a contract accepts custody of user funds at "initialization"/placement time without guaranteeing any withdrawal path exists.

### Finding Description
`placeOrder` (`evm/src/apps/IntentGatewayV2.sol:162-383`) stamps the order, computes the commitment, and transfers/escrows the caller's `order.inputs` into `_orders[commitment][token]` — it never calls `_instance(order.destination)` or otherwise checks that a gateway is registered for the destination chain: [1](#0-0) [2](#0-1) 

This is confirmed by the test suite: placing an order whose `destination` is an unregistered/unknown chain (`unknownDestination = bytes("UNKNOWN_CHAIN")`) succeeds without reverting, escrows tokens, and even collects protocol fees normally: [3](#0-2) 

Once escrowed, the only recovery paths for a cross-chain order are `cancelOrder`, which dispatches to one of three internal handlers: [4](#0-3) 

- **Same-chain path** (`_cancelSameChain`) does not apply, since `order.source != order.destination` for a genuine cross-chain order.
- **Source-side path** (`_cancelFromSource`) resolves the destination's gateway address via `_instance(order.destination)` before building the GET request key — this reverts with `UnknownInstance()` when no gateway is registered for that destination: [5](#0-4) [6](#0-5) 
- **Destination-side path** (`_cancelFromDest`) requires a live `IntentGatewayV2` deployment on the destination chain to be called in the first place; if none was ever deployed/registered there, this entry point is simply unreachable.

So for an order whose destination has no registered gateway instance, every cancellation route either reverts (`UnknownInstance`) or cannot be invoked at all. The escrow entry in `_orders[commitment][token]` (`evm/src/apps/intentsv2/IntentsBase.sol:140`) has no other release mechanism — `_withdraw`/`withdraw` are only reachable from `onAccept` (authenticated Hyperbridge messages) or the cancel paths above, none of which can complete.

### Impact Explanation
Escrowed input tokens (any ERC-20 or native asset a user places as `order.inputs`) become permanently locked in the `IntentGatewayV2` contract with no path to withdrawal — total, irrecoverable loss of the user's escrowed principal, exactly matching the bounty's "stealing or loss of funds" / "logic attacks" category. This is not a peripheral or UI concern: the on-chain contract itself lacks a guard that should exist before it accepts custody of funds tied to a specific remote routing target.

### Likelihood Explanation
`placeOrder` and `cancelOrder` are both fully public, unprivileged entry points — no relayer, prover, governance, or admin action is required to trigger the bug, and no malicious peer assumption is needed. It can be hit either by an honest user submitting a destination identifier for which Hyperbridge governance has not yet (or will never) register a gateway instance (typo, stale SDK config, chain not yet supported, deprecated chain id), or by a malicious dApp/integration front-end steering victims toward such destinations. Given `_addDeployment`/instance registration is a slow, governance-gated, asynchronous process (`NewDeployment` via `onAccept`, `evm/src/apps/IntentGatewayV2.sol` `RequestKind.NewDeployment` handling) relative to the fully permissionless `placeOrder`, the window in which a user can escrow funds against an unregistered destination is realistic and not merely theoretical.

### Recommendation
`placeOrder` should require `_instance(order.destination) != address(0)` (or equivalently call `_instance` and let it revert with `UnknownInstance`) before escrowing any tokens, for any order where `order.destination != order.source`. This closes the gap symmetrically with the existing check already performed at cancel time, ensuring escrow is never taken unless a recovery/settlement path is guaranteed to exist.

### Proof of Concept
1. On the source chain, ensure `_instances[keccak256("UNKNOWN_CHAIN")]` is unset (default, unregistered).
2. Call `placeOrder(order, graffiti)` with `order.destination = bytes("UNKNOWN_CHAIN")` and non-zero `order.inputs` — this succeeds and escrows tokens, as shown by `testInstance`-style setup in `evm/tests/foundry/IntentGatewayV2Test.sol:2851-2890` (order placed against `unknownDestination`).
3. Attempt `cancelOrder(order, options)`:
   - `isSameChain` is false (`order.source != order.destination`).
   - `currentChain == orderSource` routes to `_cancelFromSource`, which calls `_instance(order.destination)` → reverts `UnknownInstance()` (`evm/src/apps/intentsv2/IntentsBase.sol:358-362`, invoked from `evm/src/apps/intentsv2/ExtrinsicIntents.sol:188-223`).
   - There is no `IntentGatewayV2` deployed on `"UNKNOWN_CHAIN"`, so `_cancelFromDest` can never be invoked.
4. The escrowed `order.inputs` remain in `_orders[commitment][token]` indefinitely with no further callable path to release them.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L162-196)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
        if (order.inputs.length == 0) revert InvalidInput();

        // Reject duplicate output tokens 
        uint256 outputsLen_ = order.output.assets.length;
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                if tload(token) {
                    mstore(0, 0xb4fa3fb3) // InvalidInput.selector
                    revert(0x1c, 0x04)
                }
                tstore(token, 1)
            }
            unchecked {
                ++i;
            }
        }
        // Clean up transient storage so repeated placeOrder calls in the same tx don't false-positive.
        for (uint256 i; i < outputsLen_;) {
            bytes32 token = order.output.assets[i].token;
            assembly ("memory-safe") {
                tstore(token, 0)
            }
            unchecked {
                ++i;
            }
        }

        address hostAddr = host();
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;

        uint256 inputsLen = order.inputs.length;
```

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L470-491)
```text
    function cancelOrder(Order calldata order, CancelOptions calldata options) public payable nonReentrant {
        bytes32 commitment = keccak256(abi.encode(order));

        if (_filled[commitment] != address(0)) revert Filled();

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain) {
            _cancelSameChain(order, commitment);
        } else if (currentChain == orderSource) {
            _cancelFromSource(order, options, commitment);
        } else if (currentChain == orderDest) {
            _cancelFromDest(order, options, commitment);
        } else {
            revert WrongChain();
        }
    }
}
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L2851-2890)
```text
        bytes memory unknownDestination = bytes("UNKNOWN_CHAIN");

        Order memory order = Order({
            user: bytes32(0),
            source: bytes(""),
            destination: unknownDestination,
            deadline: block.timestamp + 1 hours,
            nonce: 0,
            fees: 0,
            session: address(0),
            predispatch: DispatchInfo({assets: new TokenInfo[](0), call: ""}),
            inputs: new TokenInfo[](1),
            output: PaymentInfo({beneficiary: bytes32(uint256(uint160(user))), assets: new TokenInfo[](1), call: ""})
        });

        order.inputs[0] = TokenInfo({token: bytes32(uint256(uint160(address(usdc)))), amount: inputAmount});
        order.output.assets[0] = TokenInfo({token: bytes32(uint256(uint160(address(dai)))), amount: 2000 * 1e18});

        vm.startPrank(user);
        usdc.approve(address(customGateway), inputAmount);

        vm.recordLogs();
        customGateway.placeOrder(order, bytes32(0));
        vm.stopPrank();

        // Verify DustCollected event was emitted with default fee (1%)
        Vm.Log[] memory entries = vm.getRecordedLogs();
        uint256 collectedFee = 0;

        for (uint256 i = 0; i < entries.length; i++) {
            if (entries[i].topics[0] == keccak256("DustCollected(address,uint256)")) {
                (address token, uint256 amount) = abi.decode(entries[i].data, (address, uint256));
                if (token == address(usdc)) {
                    collectedFee = amount;
                }
            }
        }

        assertEq(collectedFee, expectedDefaultFee, "Should use default protocol fee when destination fee not set");
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L188-223)
```text
    function _cancelFromSource(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();

        if (options.height <= order.deadline) revert NotExpired();

        uint256 inputsLen = order.inputs.length;
        for (uint256 i; i < inputsLen;) {
            if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();

            unchecked {
                ++i;
            }
        }

        bytes memory context =
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

        bytes[] memory keys = new bytes[](1);
        keys[0] = bytes.concat(abi.encodePacked(_instance(order.destination)), _calculateCommitmentSlotHash(commitment));
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
            height: options.height,
            fee: options.relayerFee,
            context: context,
            payer: msg.sender
        });

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L352-362)
```text
    /**
     * @dev Resolves the IntentGateway instance address for a given state machine.
     * Reverts with `UnknownInstance` if no remote deployment has been registered for that chain.
     * @param stateMachineId The raw state machine identifier bytes.
     * @return The gateway address for the given state machine.
     */
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```
