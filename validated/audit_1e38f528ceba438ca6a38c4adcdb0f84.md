Found a real local analog in `IntentsBase.sol` / `ExtrinsicIntents.sol` of the Hyperbridge intents system: an authorization value (`_instances[chain]`) that is captured/used for cross-chain authentication can be reassigned by governance via `NewDeployment` while orders that were placed against the old instance are still in flight, permanently blocking their settlement — the same "role reassigned after workflow started" invariant break described in the Sherlock report.

### Title
Order settlement (RedeemEscrow/RefundEscrow) becomes permanently unclaimable if the counterpart gateway instance is redeployed while orders are in flight - (File: evm/src/apps/intentsv2/IntentsBase.sol, evm/src/apps/intentsv2/ExtrinsicIntents.sol)

### Summary
Cross-chain intent settlement in `IntentGatewayV2`/`ExtrinsicIntents` authenticates incoming `RedeemEscrow`/`RefundEscrow` messages by checking that the message's `from` address equals the *current* value of `_instances[keccak256(source)]` [1](#0-0) . That mapping is a single latest-value slot updated in place by `_addDeployment` whenever Hyperbridge governance registers a new gateway for a state machine [2](#0-1) . Orders placed on the source chain reference the gateway instance that existed at order-placement time (via `_instance(order.destination)` when dispatching the fill/cancel message) [3](#0-2) , but the *source* chain's authentication of the returning settlement message only recognizes whichever gateway address is currently registered — there is no per-order/per-commitment binding of the counterpart address at order-placement time.

### Finding Description
When a user places an order, the input tokens are escrowed on the source chain keyed only by `commitment` in `_orders[commitment][token]` [4](#0-3) . Later, when a solver fills the order cross-chain, the destination gateway dispatches a `RedeemEscrow` POST request back to `order.source`, addressed to `_instance(order.source)` — i.e., whatever address is registered in `_instances` for the source chain *at fill time* [3](#0-2) . On the source chain, `onAccept` calls `_authenticate(incoming.request)`, which requires `incoming.request.from == _instances[keccak256(incoming.request.source)]` [1](#0-0)  — i.e. it checks the sender against whatever is *currently* registered for that chain, not what was registered when the order was placed.

If Hyperbridge governance redeploys/rotates the destination gateway (calling `_addDeployment` to point `_instances[destChain]` at a new address) while orders that were placed against the old destination gateway are still pending fill/cancel, two failure modes occur:
1. A fill/cancel dispatched from the *old* (now-unregistered) destination-chain gateway will produce a POST request whose `from` no longer matches the new `_instances[sourceChain]`-recognized address relationship consistently across both ends, because each side's `_instances` map is updated independently and asynchronously.
2. More directly: escrow tokens locked on the source chain for orders whose `order.destination` points at an instance that has since been superseded can never be released, because the destination side that would legitimately fill/cancel them and dispatch the authenticating message is bound to whatever `_instances` value exists *at the time it acts*, while the source side's `_authenticate` check is bound to whatever value `_instances` holds *at the time the message arrives* — these are not guaranteed to correspond to the same deployment epoch as the one the order was created under.

This directly mirrors the Sherlock finding's broken invariant: an authorization/identity binding that is implicitly tied to a "who was valid at initiation time" but enforced against "who is valid now," with no mechanism to fall back to the value valid when the state transition (order placement) began. The `cancelOrder` cross-chain path is similarly exposed: it looks up `instance(order.destination)` fresh at cancel time to build the GET request key [5](#0-4) , so a mid-flight instance change can point the proof query at the wrong (new) contract's storage, causing the query to find no matching commitment and the cancellation/refund path to fail permanently for that order.

### Impact Explanation
Funds already escrowed in `_orders[commitment][token]` on the source chain (user funds, ERC-20 or native) can become permanently locked: neither `fillOrder`'s cross-chain redeem path nor `cancelOrder`'s cross-chain refund path can complete once the registered instance for the relevant chain has moved on, because `_authenticate` / `_instance` resolution is always against the live mapping value, not an order-time snapshot. This is a fund-lock/loss condition consistent with the bounty's "stealing or loss of funds" and "logic attacks" categories, triggered purely by a legitimate governance action (gateway redeployment) interacting with normal, unprivileged user order flow — no malicious relayer, prover, or admin key compromise is required, only the ordinary sequence of "order placed → instance later rotated by governance → order can no longer settle."

### Likelihood Explanation
Gateway redeployment/rotation (`NewDeployment` via `UpgradeContract`/new-chain-instance registration) is a documented, expected governance operation in this contract (`RequestKind.NewDeployment`, `_addDeployment`) [6](#0-5) [2](#0-1) . Any window where an order is placed and not yet filled/cancelled while such a governance update lands on either the source or destination chain triggers the issue. Given cross-chain fill/cancel latency (state proofs, challenge periods) is non-trivial, the race window is realistic, not a contrived edge case.

### Recommendation
Bind the counterpart gateway address at order-placement time rather than resolving it fresh from `_instances` on every subsequent settlement action:
- Store the resolved destination instance address (or the current instance mapping's value) as part of the order's stored/commitment-referenced state when the order is placed, and use that snapshot for later `_authenticate` checks and dispatch routing.
- Alternatively, on `_addDeployment`, do not overwrite `_instances[chain]` in place; instead version instances and continue accepting messages from both current and previously-registered gateway addresses for a grace period until all in-flight orders referencing the prior deployment have settled or been forcibly migrated/refunded.
- Document, and ideally enforce on-chain, that gateway rotation must first drain (fill/cancel) all pending orders for the affected chain before the old instance mapping entry is removed.

### Proof of Concept
1. On chain A (source) and chain B (destination), gateways are registered: `_instances[hash(B)] = GatewayB_v1` on A, `_instances[hash(A)] = GatewayA` on B.
2. User places an order on chain A with `order.destination = B`; input tokens escrowed in `_orders[commitment][token]` on A [4](#0-3) .
3. Before the order is filled, Hyperbridge governance dispatches `NewDeployment` to chain A, calling `_addDeployment` to set `_instances[hash(B)] = GatewayB_v2` (e.g., due to an upgrade/migration) [2](#0-1) .
4. A solver fills the order on `GatewayB_v1` (the address the order was created against and that still holds the fill logic/state solvers interact with), which dispatches a `RedeemEscrow` POST back to chain A `from = GatewayB_v1` [3](#0-2) .
5. On chain A, `onAccept` → `_authenticate` checks `_instances[hash(B)] == GatewayB_v1`, but it now reads `GatewayB_v2`, so the check fails with `Unauthorized` [1](#0-0) . The escrowed tokens on chain A remain locked, and the order can never be redeemed through the normal path (the solver already delivered outputs on chain B and cannot get paid back from escrow).

I was not able to fully trace the exact operational cadence/frequency of `NewDeployment` governance actions in production (that lives in off-chain governance tooling not indexed here), so the practical frequency of the race window is inferred from the contract's documented support for redeployment rather than observed operational data.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L63-67)
```text
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L96-99)
```text
        /**
         * @dev Register a new gateway deployment for a remote state machine.
         */
        NewDeployment,
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L140-140)
```text
    mapping(bytes32 => mapping(address => uint256)) public _orders;
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L521-524)
```text
    function _addDeployment(Deployment memory body) internal {
        _instances[keccak256(body.chain)] = body.gateway;
        emit DeploymentAdded({chain: string(body.chain), gateway: body.gateway});
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L550-568)
```text
            bytes memory context =
                abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}));

            bytes[] memory keys = new bytes[](1);
            keys[0] = bytes.concat(
                // contract address
                abi.encodePacked(instance(order.destination)),
                // storage slot hash
                calculateCommitmentSlotHash(commitment)
            );
            DispatchGet memory request = DispatchGet({
                dest: order.destination,
                keys: keys,
                timeout: 0,
                height: uint64(options.height),
                fee: options.relayerFee,
                context: context,
                payer: msg.sender
            });
```
