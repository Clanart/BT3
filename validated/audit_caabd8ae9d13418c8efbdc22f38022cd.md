## Analysis Result

I have confirmed the analog through direct code inspection of `evm/src/apps/intentsv2/`. The bug-class match is strong: exactly like the `SsoAccount`/`GuardianRecoveryValidator`/`WebAuthValidator` case — where two collaborating components can be legitimately configured/attached independently, and a later (non-malicious) reconfiguration of one component silently breaks the invariant the other depends on — `IntentGatewayV2`'s `_instances` registry is looked up **live** at both authentication time (`_authenticate`, `ExtrinsicIntents.sol:63-67`) and at cancel-query time (`_cancelFromSource`, `ExtrinsicIntents.sol:188-223`), rather than being bound to the value that was in effect when the order was placed/filled.

### Title
Stale Cross-Chain Peer Binding in IntentGatewayV2 Enables Double-Benefit on Gateway Redeployment - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`, `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentGatewayV2`'s cross-chain settlement path authenticates incoming `RedeemEscrow`/`RefundEscrow` messages and constructs cancellation storage-proof queries using the **current** value of `_instances[keccak256(stateMachineId)]` [1](#0-0) , rather than a value pinned to the order at placement/fill time. When Hyperbridge governance legitimately rotates a chain's registered gateway address via `NewDeployment` (`_addDeployment`) [2](#0-1)  — a normal, expected lifecycle operation for redeploying/upgrading a gateway on a connected chain — any order that was already filled against the **old** gateway address becomes permanently unrecoverable through its intended settlement path, while the source-chain cancellation path silently treats it as unfilled and refunds the escrow to the user.

### Finding Description
Three code paths all depend on `_instances`, but none of them binds the value to a specific order:

1. **Fill authentication** (`_authenticate`): when a `RedeemEscrow`/`RefundEscrow` message arrives on the source chain, it is authenticated by checking that `request.from == _instance(request.source)` [3](#0-2) . This reads whatever gateway address is *currently* registered for the destination chain.

2. **Cancellation query** (`_cancelFromSource`): builds a storage-proof key using `_instance(order.destination)` at the time cancellation is initiated [4](#0-3) , to check whether `_filled[commitment]` is empty at that (possibly different, newly-deployed) contract.

3. **Registry mutation** (`_addDeployment`/`NewDeployment`): unconditionally overwrites `_instances[keccak256(chain)]` with a new gateway address [2](#0-1) , with no check for in-flight orders still referencing the old address, and no versioning of which gateway address an order's commitment is tied to.

**Attack/failure sequence** (no malicious actor required — only a routine gateway redeployment on the destination chain, e.g. an upgrade that changes the contract address rather than using the proxy's `UpgradeContract` path):

1. User places a cross-chain order on Chain A (source) targeting Chain B (destination), escrowing input tokens. At this time `_instances[B]` on Chain A == `OldGatewayB`.
2. Solver fills the order on Chain B against `OldGatewayB`, delivers output tokens to the beneficiary, and `OldGatewayB` dispatches `RedeemEscrow` back to Chain A addressed to `_instance(order.source)`.
3. Before this message is relayed/delivered, Hyperbridge governance dispatches `NewDeployment` to Chain A, rotating `_instances[B]` to `NewGatewayB` (a fresh contract deployment for chain B).
4. When the `RedeemEscrow` message from `OldGatewayB` finally arrives, `_authenticate` compares `request.from == OldGatewayB` against the now-current `_instance(B) == NewGatewayB` — mismatch — the message reverts with `Unauthorized()`. The solver can never collect the escrowed input tokens through the intended path.
5. Separately (or subsequently) the user calls `cancelOrder` from the source chain. `_cancelFromSource` queries `_filled[commitment]` at `_instance(B) == NewGatewayB` — a different contract that never processed this order — the slot is empty, so `onGetResponse` concludes the order was never filled and refunds the escrow to the user.
6. Net result: the user keeps the refunded escrow **and** already received the solver's output tokens on Chain B; the solver is left with real, delivered assets and no reimbursement — a fund-loss and duplicate-settlement condition caused purely by the missing binding between an order and the gateway instance in effect when it was created/filled.

### Impact Explanation
This directly matches the required impact categories: **loss of funds** (solver's delivered assets become unrecoverable) and **double-claim/double-settlement** (user receives both the solver's output and a refund of the same escrowed input). No malicious relayer, prover, or governance actor is required — `NewDeployment` is a normal, intended governance action (e.g., a routine contract migration), and the vulnerability is purely a missing invariant: the protocol never binds an order's settlement/cancellation proof target to the specific gateway address that was live when the order was created or filled.

### Likelihood Explanation
Gateway redeployments/rotations are an expected part of the protocol's operational lifecycle (the codebase already anticipates in-place upgrades via the `UpgradeContract` ERC-1967 path, implying that address-changing redeployments via `NewDeployment` are also anticipated for chain onboarding/migration). Any redeployment that happens while cross-chain orders are in-flight (a realistic window given multi-block ISMP settlement latency) triggers this bug automatically, without any attacker action — raising likelihood beyond a purely theoretical edge case.

### Recommendation
Bind the gateway instance address into the order/commitment at placement or fill time (e.g., snapshot `_instance(order.destination)` and `_instance(order.source)` into the `Order` struct or a side mapping keyed by `commitment`), and use that snapshotted value — not the live `_instances` registry — for both `_authenticate` in `onAccept` and for constructing the storage-proof key in `_cancelFromSource`. Alternatively, require `NewDeployment` to only ever be used for chains with zero in-flight (unfilled/unsettled) commitments, or maintain a versioned/historical registry (`_instances[chain][version]`) so that old orders can still authenticate/query against the gateway address that was actually used to fill them.

### Proof of Concept
Not independently executed (no filesystem/terminal access in this mode), but the trace is fully supported by the cited code:
1. `IntentsBase._addDeployment` — unconditional overwrite of `_instances[keccak256(chain)]`: [5](#0-4) 
2. `ExtrinsicIntents._authenticate` — live lookup used for `RedeemEscrow`/`RefundEscrow` authorization: [3](#0-2) 
3. `ExtrinsicIntents._cancelFromSource` — live lookup used to build the destination storage-proof key for the cancellation `GetRequest`: [4](#0-3) 
4. `onAccept` dispatch of `NewDeployment`/`RedeemEscrow`/`RefundEscrow`: [6](#0-5) 

A Foundry test reproducing steps 1–6 (deploy `OldGatewayB`, fill an order, rotate via `NewDeployment` to `NewGatewayB` before delivery, show `_authenticate` reverting on the genuine `RedeemEscrow`, then show `_cancelFromSource`/`onGetResponse` refunding the same escrow) would concretely confirm this; I was not able to run it in this session due to lack of execution tooling, so this should be validated by a Devin session with repo/build access before treating it as fully proven.

### Citations

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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L63-67)
```text
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-309)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }

        // only hyperbridge is permitted to perform these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
        } else if (kind == RequestKind.UpdateParams) {
            _updateParams(abi.decode(incoming.request.body[1:], (ParamsUpdate)));
        } else if (kind == RequestKind.SweepDust) {
            _sweepDust(abi.decode(incoming.request.body[1:], (SweepDust)));
        } else if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(incoming.request.body[1:], (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        }
    }
```
