## Analysis

The M-6 report's core defect is an **access-control check bound to a single fixed identity that becomes stale during a legitimate state transition**, causing a permission check to reject a message that is legitimate but arrives from/at an address the check no longer recognizes as valid — freezing the settlement flow and stranding funds.

The Hyperbridge analog exists in the `IntentGatewayV2` cross-chain intent settlement flow, specifically in how `ExtrinsicIntents._authenticate` validates incoming `RedeemEscrow`/`RefundEscrow` messages against `IntentsBase._instances`, a mapping that `_addDeployment` overwrites **immediately and unconditionally**, with no grace period or acceptance of the prior registered instance.

### Title
Immediate, non-versioned gateway instance overwrite in `IntentGatewayV2` permanently strands cross-chain escrow when a deployment is re-registered while an order settlement is in flight - (File: `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`_authenticate` gates `RedeemEscrow`/`RefundEscrow` acceptance on `_instance(request.source)` matching `request.from` exactly. [1](#0-0) 
`_instances` is updated in-place by `_addDeployment`, invoked from `onAccept`'s `NewDeployment` branch, with no versioning or delay. [2](#0-1) [3](#0-2) 
If the registered instance for a chain changes while a `RedeemEscrow`/`RefundEscrow` message dispatched under the *old* instance is still in flight (during the ISMP relay/finality window), the message will be rejected on arrival because the current `_instances` mapping no longer matches the sender that legitimately dispatched it — exactly the "old vs. new authorized identity" mismatch that broke `LendingPool#flashAction` in the M-6 report.

### Finding Description
Cross-chain fills work as follows: a solver calls `fillOrder` on the destination chain, `_fillCrossChain` immediately marks the order `_filled[commitment] = msg.sender` [4](#0-3) 
pays the beneficiary, and dispatches a `RedeemEscrow` POST request back to `order.source`, addressed to `_instance(order.source)` as currently known on the destination chain. [5](#0-4) 
On the source chain, `onAccept` receives this POST and calls `_authenticate`, which requires `request.from` (the module address that dispatched the message on the destination chain) to equal the *currently* registered `_instance(request.source)`. [6](#0-5) 

`_instances` is a single mapping slot per chain, and `_addDeployment` simply assigns over it with no history, timelock, or acceptance of an outgoing/incoming grace window: [7](#0-6) 

Because ISMP messages traverse a consensus/challenge-period relay window before being delivered (this is the entire point of the state-proof/finality model), there is a real window in which:
1. Solver fills order on chain B, dispatching `RedeemEscrow` addressed from B's *current* gateway instance address.
2. Hyperbridge governance dispatches `NewDeployment` to chain A (e.g. gateway B is redeployed/migrated for an upgrade), updating `_instances[keccak256(B)]` on chain A to the new address, via the same `onAccept` governance path. [3](#0-2) 
3. The in-flight `RedeemEscrow` message, once relayed and proven, arrives at chain A's `onAccept`. `_authenticate` now compares `request.from` (old B address) against `_instance(B)` (new address) — they no longer match, so it reverts `Unauthorized`.

The corrupted/stale value is `_instances[keccak256(order.source-or-destination)]`: it is treated as a single point-in-time authorization key with no continuity guarantee across the asynchronous multi-block relay window that ISMP requests are designed to tolerate.

### Impact Explanation
The escrow release (`_withdraw`) never executes because `onAccept` reverts before reaching it. [8](#0-7) 
On chain B the order is already irreversibly marked filled (`_filled[commitment] = msg.sender`) and the solver has already paid the beneficiary out of pocket, so the solver cannot be made whole. Meanwhile on chain A the user's escrowed input tokens remain locked under `_orders[commitment][token]` with no code path to release them: `_cancelFromSource` cannot succeed either, since its GET-based check queries the `_filled` slot on the destination chain, which is now non-empty (`Filled()` revert path). [9](#0-8) 
The result is fund loss/permanent lock for the solver (unreimbursed) and the user (escrow inaccessible to any legitimate party), a direct violation of the "funds must move exactly once and only to the rightful beneficiary" invariant.

### Likelihood Explanation
This requires only a routine, legitimate operational action — Hyperbridge governance re-registering/updating a chain's gateway instance (`NewDeployment`), which the protocol explicitly supports as a normal maintenance/upgrade mechanism — occurring while any cross-chain order settlement message is still traversing the relay/finality window. Given ISMP's multi-block consensus/challenge-period design, this window is not instantaneous, and gateway redeployments are an expected operational event over the protocol's lifetime, making the race condition realistically reachable without any malicious actor.

### Recommendation
`_authenticate` should not rely solely on the *current* value of `_instances[chain]`. Either (a) retain a bounded history of previously valid instance addresses per chain (e.g., accept both current and immediately-prior instance for a governance-defined grace window) before applying a new deployment, or (b) require in-flight settlement messages dispatched under an old instance to be drained/finalized before the new deployment takes effect, or (c) have `NewDeployment` fail/queue if it would invalidate an instance with outstanding un-settled orders bound to it.

### Proof of Concept
1. Deploy `IntentGatewayV2` on chains A and B, `initialize` registers each as the other's peer instance.
2. User calls `placeOrder` on A with `destination = B`, escrowing input tokens; commitment `C` is emitted.
3. Solver calls `fillOrder` on B; `_fillCrossChain` sets `_filled[C] = solver`, pays beneficiary, and dispatches `RedeemEscrow` to A addressed `to: _instance(A)` (call this dispatch has `from = B_old`). [10](#0-9) 
4. Before the `RedeemEscrow` message is relayed/proven on A, Hyperbridge dispatches `NewDeployment{chain: B, gateway: B_new}` to A's gateway; `onAccept` calls `_addDeployment`, setting `_instances[keccak256(B)] = B_new`. [2](#0-1) 
5. Relayer submits the `RedeemEscrow` proof to A. `onAccept` → `_authenticate` computes `module = B_old` from `request.from`, compares to `_instance(B) = B_new` → mismatch → revert `Unauthorized`. [1](#0-0) 
6. Escrowed tokens for commitment `C` remain locked in `_orders[C][token]` on A indefinitely; `cancelOrder` from A reverts with `Filled()` since B's `_filled[C]` is non-empty. [11](#0-10)

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L63-67)
```text
    function _authenticate(PostRequest calldata request) internal view {
        if (request.from.length != 20) revert InvalidInput();
        address module = address(bytes20(request.from));
        if (_instance(request.source) != module) revert Unauthorized();
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-92)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L289-296)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }

```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L297-300)
```text
        // only hyperbridge is permitted to perform these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-425)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
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

        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }

            if (isRefund) {
                emit EscrowRefunded({commitment: body.commitment, tokens: body.tokens});
            } else {
                emit EscrowReleased({commitment: body.commitment, tokens: body.tokens});
            }
        }
    }
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L515-524)
```text
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
