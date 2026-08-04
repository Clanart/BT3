### Title
Stale `_instances` gateway mapping permanently locks in‑flight cross‑chain escrow after a `NewDeployment` update - (File: `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
### Finding Description
`IntentGatewayV2` caches, per chain, the address of the peer `IntentGateway` instance in a mutable mapping `_instances[keccak256(stateMachineId)] = gatewayAddress`, updated only through a governance-originated `NewDeployment` request delivered via `onAccept`: [1](#0-0) [2](#0-1) 

This mapping is used both to (a) authenticate inbound `RedeemEscrow`/`RefundEscrow` requests — `if (instance(request.source) != module) revert Unauthorized();` — and (b) to compute the destination `to` address when a solver dispatches a redeem/refund message after filling or cancelling an order: [3](#0-2) [4](#0-3) [5](#0-4) 

The `Order` struct, however, is fully committed at `placeOrder` time — its `order.source`/`order.destination` (state-machine identifiers) are fixed and the escrow commitment (`keccak256(abi.encode(order))`) never records which concrete gateway *address* was authoritative for the peer chain at that moment: [6](#0-5) [7](#0-6) 

This is the exact analog of the reported bug class: a dependent object (`Order`/escrow) is created against a peer contract reference that is cached elsewhere and can later be changed by an unrelated privileged update (`NewDeployment`), while the object itself has no way to be reconciled to the new reference. Concretely:

1. A user places a cross-chain order on chain A escrowing funds; `order.source`/`order.destination` reference chain A/chain B by state-machine ID only.
2. Before the order is filled or cancelled, Hyperbridge governance dispatches a `NewDeployment` request updating `_instances[chainB]` on chain A (e.g., migrating gateway B to a new address) — a legitimate, expected operational action, not an attacker action.
3. A solver later fills the order on the (old or new) gateway B and dispatches a `RedeemEscrow` request whose `to` field is computed from B's *current* view of `instance(order.source)` and whose `from`/module identity is B's own address.
4. When this message lands on chain A's gateway, `onAccept` → `authenticate()` checks `instance(request.source) != module`. Because A's `_instances[chainB]` was updated mid-flight relative to what the order/fill assumed, the module address embedded in the request no longer equals A's freshly-updated mapping value, so `authenticate()` reverts with `Unauthorized()`: [8](#0-7) 

The escrowed input tokens in `_orders[commitment][token]` remain permanently locked in the contract with no user-callable path to retrieve them — the withdraw path is only reachable from `onAccept` after `authenticate()` succeeds: [9](#0-8) 

### Impact Explanation
Escrowed user funds for any order in flight at the moment of a `NewDeployment`/gateway-migration update become unredeemable through the normal protocol path: solvers cannot claim their fill reward, and users cannot get refunds via `cancelOrder`, since both routes rely on `authenticate()` matching the (now stale) `_instances` value against the request's embedded module address. This mirrors the original report's impact precisely — legitimate participants lose the ability to claim what is owed to them, and recovery would require an out-of-band, manual, governance-mediated fix (e.g., another cross-chain governance message correcting state), which is exactly the "manual/expensive admin intervention" impact called out in the seed report. This is a loss/lock of bridged escrow funds, not merely a gas/DoS nuisance.

### Likelihood Explanation
`NewDeployment` gateway migrations are an expected, documented operational lifecycle event for the IntentGateway (used when redeploying/upgrading a gateway instance on a chain), so the window where in-flight orders exist against a soon-to-be-stale peer reference is realistically triggered during normal operations, not exotic attacker behavior. No malicious relayer, prover, or admin action is required — only the ordinary combination of (a) an in-flight order and (b) a routine governance-driven `NewDeployment` update landing before that order settles.

### Recommendation
Bind each order to the specific peer gateway address that was authoritative at placement time (e.g., include the resolved `instance(order.destination)`/`instance(order.source)` address inside the order/commitment, or store it alongside `_orders[commitment]`), and have `authenticate()` validate incoming redeem/refund requests against that order-specific recorded instance rather than the live, mutable `_instances` mapping. Alternatively, provide a governance-only "migration" path that atomically remaps outstanding commitments' expected instance address when `NewDeployment` is processed, so in-flight escrows are never silently orphaned.

### Proof of Concept
1. Deploy `IntentGatewayV2` on chain A and chain B; register `_instances[B] = gatewayB_v1` on A via a `NewDeployment` message.
2. User calls `placeOrder` on chain A with `order.destination = B`; funds are escrowed in `_orders[commitment]` on A.
3. Before the order is filled, Hyperbridge governance sends a new `NewDeployment` message updating `_instances[B] = gatewayB_v2` on A (e.g. gateway B redeployed/migrated).
4. Solver fills the order on `gatewayB_v1` (or v2, whichever the fill was prepared against) and dispatches `RedeemEscrow` with `from = gatewayB_v1`.
5. On chain A, `onAccept` → `authenticate()` computes `instance(order.source)` = `gatewayB_v2` ≠ `module (gatewayB_v1)` → reverts `Unauthorized()`.
6. `_orders[commitment][token]` remains non-zero forever; neither the solver nor the user can retrieve the escrowed tokens via `fillOrder`/`cancelOrder` flows.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L142-146)
```text
    /**
     * @dev Maps keccak256(stateMachineId) to the registered gateway address for
     * that chain. Used for authenticating cross-chain messages and routing dispatches.
     */
    mapping(bytes32 => address) public _instances;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L391-410)
```text
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L620-634)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return withdraw(body, kind == RequestKind.RefundEscrow);
        }

        // only hyperbridge is permitted to perfom these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            NewDeployment memory body = abi.decode(incoming.request.body[1:], (NewDeployment));
            _instances[keccak256(body.stateMachineId)] = body.gateway;

            emit NewDeploymentAdded({stateMachineId: body.stateMachineId, gateway: body.gateway});
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L139-155)
```text
        address hostAddr = host();
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RedeemEscrow)),
            abi.encode(
                WithdrawalRequest({
                    commitment: commitment, tokens: order.inputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
                })
            )
        );
        DispatchPost memory request = DispatchPost({
            dest: order.source,
            to: abi.encodePacked(_instance(order.source)),
            body: body,
            timeout: 0,
            fee: options.relayerFee,
            payer: msg.sender
        });
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

**File:** evm/src/apps/IntentGatewayV2.sol (L191-194)
```text
        address hostAddr = host();
        order.user = bytes32(uint256(uint160(msg.sender)));
        order.source = IDispatcher(hostAddr).host();
        order.nonce = _nonce++;
```

**File:** evm/src/apps/IntentGatewayV2.sol (L326-343)
```text
            order.inputs = reducedInputs;
            commitment = keccak256(abi.encode(order));
        } else {
            reducedInputs = order.inputs;
            commitment = keccak256(abi.encode(order));
        }

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
