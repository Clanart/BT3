Confirmed: `placeOrder` in `IntentGatewayV2.sol` never checks that `order.destination` is a registered `_instances` entry before escrowing the user's tokens.

### Title
`placeOrder` escrows funds for cross-chain orders with an unregistered destination, permanently locking user funds - ([File: evm/src/apps/IntentGatewayV2.sol])

### Summary
`IntentGatewayV2.placeOrder()` accepts an arbitrary `order.destination` state-machine id supplied by the caller and immediately escrows the user's input tokens, without ever checking that a gateway `Deployment` is registered for that destination via `_instances`. Registration of a destination gateway (`_addDeployment`, gated to Hyperbridge-only `NewDeployment` messages) is the analog of the reported "registration" precondition. Just as the external report shows a game allowing spins/deposits before registration, `placeOrder` allows escrow deposits for a destination that is not (and may never be) a recognized peer.

### Finding Description
`placeOrder` [1](#0-0)  stamps and escrows the order purely from caller-supplied `order.destination`/`order.output` data; it never calls `_instance(order.destination)` or otherwise validates that `_instances[keccak256(order.destination)]` is non-zero. Escrow bookkeeping (`_orders[commitment][token]`) is credited unconditionally at [2](#0-1) .

The only place destination validity is checked is deep in the fill/cancel cross-chain paths: `_fillCrossChain` resolves `_instance(order.source)` when dispatching the `RedeemEscrow` message back [3](#0-2) , and `_cancelFromSource` resolves `_instance(order.destination)` when dispatching the cancellation GET request [4](#0-3) . Both calls go through `_instance()`, which reverts with `UnknownInstance` if no deployment is registered for that chain id [5](#0-4) .

Because `placeOrder` performs no equivalent registration check before escrowing, a user (or an order relayed on their behalf) can create a cross-chain order whose `destination` is any arbitrary/unregistered state-machine id:
- No solver can fill it: a legitimate `fillOrder` cross-chain fill must occur on the actual destination chain's `IntentGatewayV2` contract; if that id was never registered/deployed as a real peer, no contract exists there to run `fillOrder`/`_fillCrossChain`, so the order can never be filled.
- The primary cancellation path, `_cancelFromSource`, unconditionally calls `_instance(order.destination)` while building the GET dispatch keys, which reverts `UnknownInstance` for an unregistered destination, permanently blocking the only recovery path for this order class.

The result: input tokens are irreversibly stuck in the contract's `_orders` escrow mapping with no way to release them — the order can be neither filled nor cancelled once the deadline check logic routes it through `_cancelFromSource`'s `_instance` lookup.

### Impact Explanation
This is a direct loss/lock of user funds reachable by any unprivileged caller through the public `placeOrder` entrypoint — no relayer, prover, admin, or governance compromise is required. It matches the bounty's "stealing or loss of funds" and "unauthorized... transaction manipulation" categories: escrow custody state is created and can become permanently unrecoverable due to a missing registration/validity guard, mirroring the reported bug class (state-changing entrypoint reachable without the mandatory registration check enforced elsewhere in the same subsystem).

### Likelihood Explanation
High likelihood of accidental occurrence (any typo'd or not-yet-onboarded destination chain id in a client/SDK integration bricks the order), and it is trivially triggerable by an attacker who wants to grief a specific user's flow or simply demonstrate fund lock, since `placeOrder` has zero gating on `order.destination` validity.

### Recommendation
Add a registration check in `placeOrder` (and ideally `fillOrder`/`cancelOrder`'s routing) requiring `_instances[keccak256(order.destination)] != address(0)` (i.e., calling `_instance(order.destination)` and reverting otherwise) before any tokens are pulled into escrow, mirroring the `isOldEnough`-style precondition enforcement recommended in the source report.

### Proof of Concept
1. Attacker (or naive integrator) calls `placeOrder(order, graffiti)` with `order.destination` set to a state-machine id that has never received a `NewDeployment` (`_addDeployment`) message, e.g., an arbitrary/garbage bytes value.
2. `placeOrder` escrows `order.inputs` into `_orders[commitment][token]` unconditionally [2](#0-1) .
3. No solver contract exists at the unregistered destination, so the order is never filled.
4. Attacker/user calls `cancelOrder(order, options)` before or after the deadline; since `orderSource != orderDest`, it routes to `_cancelFromSource` [6](#0-5) , which calls `_instance(order.destination)` while building the GET request keys [7](#0-6)  — this reverts `UnknownInstance()` every time.
5. Escrowed tokens for this commitment remain permanently locked in the contract with no fill path and no functioning cancel path.

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

**File:** evm/src/apps/IntentGatewayV2.sol (L470-490)
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
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L148-155)
```text
        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L205-215)
```text
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
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
```
