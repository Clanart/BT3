### Title
Stranded intent escrow after gateway redeployment/instance update — settlement messages route to the wrong contract instance - ([File: evm/src/apps/intentsv2/IntentsBase.sol])

### Summary
The Intent Gateway resolves the cross-chain peer address for a settlement message (`RedeemEscrow`/`RefundEscrow`) from a live, governance-mutable `_instances` registry at *dispatch time*, rather than binding it to the instance that actually escrowed the user's funds at order-placement time. When Hyperbridge governance registers a new gateway deployment for a chain (`NewDeployment`/`add_deployment`) — a normal, expected operational action, not an attack — settlement messages for orders that are still in flight get routed to the *new* instance address instead of the original contract that holds the escrow. This is the same broken invariant as the RocketPool report: a version/registry change that occurs while a bonded/escrowed operation is pending makes the finalize path unreachable, permanently locking funds with no recovery function.

### Finding Description
`IntentsBase._instances` maps `keccak256(stateMachineId) → gateway address`, updated via governance-only `NewDeployment` requests [1](#0-0) . Every cross-chain settlement dispatch (`RedeemEscrow` on the destination-to-source fill path, `RefundEscrow` on the destination-to-source cancel path) builds the outbound message's `to` field from the *current* value of this registry at the moment of dispatch: [2](#0-1) 

and on the source-cancel GET path the same pattern is used to compute the storage-proof key against `instance(order.destination)` [3](#0-2) .

Incoming settlement handling then authenticates strictly against the *current* registry entry as well:
`authenticate()`/`_authenticate()` requires `instance(request.source) == request.from` [4](#0-3) , and `onAccept` only proceeds to `withdraw()` (which pays out escrow) after this check [5](#0-4) .

Break scenario:
1. A user places a cross-chain order on chain A; tokens are escrowed in `_orders[commitment]` on the gateway instance deployed at address `G_A_old`.
2. Before the order is filled/cancelled, Hyperbridge governance calls `add_deployment` to register (or redeploy) the gateway for chain A/B, updating `_instances[keccak256(A)]` on chain B (and vice versa) to a new address `G_A_new` [6](#0-5) .
3. A solver fills the order on chain B. The `RedeemEscrow` message is dispatched with `to = instance(order.source)`, which now resolves to `G_A_new`, not `G_A_old` where the escrow actually lives [2](#0-1) .
4. The ISMP host delivers the message to `G_A_new`. That contract has no record of `_orders[commitment]`; `withdraw()` reverts (`UnknownOrder` — same check pattern shown at [7](#0-6) ). The delivery is retried by relayers but will always target `G_A_new` because the `to` address is fixed at dispatch and derived from the registry, so it will never reach `G_A_old`.
5. The escrowed funds in `G_A_old` are now permanently unreachable: `G_A_old._orders[commitment]` still holds the tokens, but no future settlement/cancel message can ever be routed there once the registry has moved on, and there is no user-callable rescue/withdraw function independent of an authenticated cross-chain message.

This mirrors RocketPool's finding precisely: a legitimate, non-malicious "version/registry" update performed mid-flight breaks the finalize path for in-flight escrowed value, and the codebase provides no fallback to reclaim funds bound to the stale instance.

### Impact Explanation
This is a direct loss/lock of user funds (escrowed order inputs and accrued fees) with no attacker required — it fits the bounty's "stealing or loss of funds" and "false proof/state acceptance" categories because the mismatch causes legitimate settlement proofs to be rejected/misrouted for orders whose registry snapshot has gone stale, permanently locking real user assets in the gateway contract. Given that Intent Gateway deployments are expected to be added/updated over time as Hyperbridge onboards more chains (a routine governance operation, evidenced by the dedicated `add_deployment` extrinsic and `NewDeployment` request kind), any order in flight at the moment of such an update is at risk.

### Likelihood Explanation
Moderate-to-high: `add_deployment`/gateway redeployment is a normal, anticipated operational lifecycle event (the pallet has first-class support and tests for it — `add_deployment_notifies_existing_gateways`), and cross-chain orders naturally have non-zero lifetime between placement and fill/cancel (auction + relaying + proof delay). Any registry update landing inside that window strands the corresponding in-flight orders. No attacker action, malicious relayer, or governance compromise is needed — only the intersection of a routine deployment update and normal cross-chain settlement latency.

### Recommendation
Bind the settlement message's peer address at order-placement time (store the resolved instance address in the `Order`/commitment data, or snapshot `_instances[keccak256(chain)]` into per-order state) rather than re-resolving it from the live registry at fill/cancel/dispatch time. Alternatively, retain historical instance addresses so that authentication and dispatch accept messages tied to any previously-valid instance for a given order's commitment, and add an explicit rescue path allowing the original escrow holder contract to release funds once the escrow's chain-of-authenticity from the *new* registry can be independently verified (e.g., admin/governance-gated recovery keyed by proof of non-fulfillment), matching RocketPool's own remediation of "let users retrieve their bond after the version change is finalized."

### Proof of Concept
Conceptual reproduction using existing test scaffolding style (`evm/tests/foundry/IntentGatewayV2Test.sol`):
1. Deploy `IntentGatewayV2` at `G_A_old` on chain A and `G_B` on chain B; register each in the other's `_instances` via `NewDeployment`.
2. User places a cross-chain order on chain A (`source=A`, `destination=B`); tokens escrow into `G_A_old._orders[commitment]`.
3. Simulate governance calling `onAccept` with `RequestKind.NewDeployment` on chain B pointing chain A's instance to a newly deployed `G_A_new` (mirrors `testOnAcceptNewDeployment`, [8](#0-7) ).
4. Have the solver call `fillOrder` on `G_B`; observe the dispatched `RedeemEscrow` request's `to` field now equals `G_A_new`, not `G_A_old`.
5. Simulate delivery of that request via `host.onAccept`/`dispatchIncoming` to `G_A_new`; observe revert (`UnknownOrder`) because `G_A_new._orders[commitment] == 0`.
6. Confirm `G_A_old._orders[commitment]` still holds the escrowed balance and that no further code path can deliver a valid `RedeemEscrow`/`RefundEscrow` referencing `G_A_old`, since all future dispatches resolve `to` via the now-updated `_instances` mapping.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L514-524)
```text
    /**
     * @dev Registers a new IntentGateway deployment for a remote state machine.
     * Called when Hyperbridge governance adds support for a new chain. The gateway
     * address is stored in `_instances` keyed by the hash of the state machine ID.
     *
     * @param body The deployment info containing the state machine ID and gateway address.
     */
    function _addDeployment(Deployment memory body) internal {
        _instances[keccak256(body.chain)] = body.gateway;
        emit DeploymentAdded({chain: string(body.chain), gateway: body.gateway});
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L202-216)
```text
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

```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L247-259)
```text
        bytes memory body = bytes.concat(
            bytes1(uint8(RequestKind.RefundEscrow)),
            abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-295)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L286-294)
```text
    /**
     * @dev Checks that the request originates from a known instance of the IntentGateway.
     */
    function authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        // IntentGateway only accepts incoming assets from itself or known instances
        if (instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-691)
```text
    function withdraw(WithdrawalRequest memory body, bool isRefund) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        _filled[body.commitment] = beneficiary;

        // redeem escrowed tokens
        uint256 len = body.tokens.length;
        for (uint256 i; i < len;) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (_orders[body.commitment][token] == 0) revert UnknownOrder();
```

**File:** modules/pallets/intents-coprocessor/src/lib.rs (L371-401)
```rust
		pub fn add_deployment(
			origin: OriginFor<T>,
			state_machine: StateMachine,
			gateway: H160,
			params: IntentGatewayParams,
		) -> DispatchResult {
			T::GovernanceOrigin::ensure_origin(origin)?;

			// Store gateway info
			let gateway_info = GatewayInfo { gateway, params };

			Gateways::<T>::insert(state_machine, gateway_info);

			// Notify all existing gateways about the new deployment
			// Only notify gateways with different addresses (same address automatically accepts)
			for (existing_state_machine, existing_gateway_info) in Gateways::<T>::iter() {
				// Skip if same state machine or same gateway address
				if existing_state_machine == state_machine
					|| existing_gateway_info.gateway == gateway
				{
					continue;
				}

				// Prepare cross-chain request to notify existing gateway
				let new_deployment =
					types::NewDeployment { chain: state_machine.to_string().into_bytes(), gateway };
				let request = RequestKind::AddDeployment(new_deployment);
				let body = request.encode_body();

				// Dispatch cross-chain message (ignore errors to not fail the whole operation)
				let _ = Self::dispatch(existing_state_machine, existing_gateway_info.gateway, body);
```

**File:** evm/tests/foundry/IntentGatewayV2Test.sol (L2493-2531)
```text
    function testOnAcceptNewDeployment() public {
        bytes memory stateMachineId = bytes("NEW_CHAIN");
        address gateway = address(0x1234);

        Deployment memory deployment = Deployment({chain: stateMachineId, gateway: gateway});

        bytes memory body = bytes.concat(bytes1(uint8(IntentsBase.RequestKind.NewDeployment)), abi.encode(deployment));

        PostRequest memory request = PostRequest({
            source: host.hyperbridge(),
            dest: host.host(),
            nonce: 0,
            from: abi.encodePacked(address(intentGateway)),
            to: abi.encodePacked(address(intentGateway)),
            body: body,
            timeoutTimestamp: 0
        });

        vm.recordLogs();

        vm.prank(address(host));
        intentGateway.onAccept(IncomingPostRequest({relayer: address(0), request: request}));

        // Check DeploymentAdded event
        Vm.Log[] memory entries = vm.getRecordedLogs();
        bool eventFound = false;

        for (uint256 i = 0; i < entries.length; i++) {
            if (entries[i].topics[0] == keccak256("DeploymentAdded(string,address)")) {
                eventFound = true;
                break;
            }
        }

        assertTrue(eventFound, "DeploymentAdded event should be emitted");

        // Verify instance was stored
        assertEq(intentGateway.instance(stateMachineId), gateway, "Gateway instance should be stored");
    }
```
