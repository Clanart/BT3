Based on the local code, the `oft_adapter_fa.move` escrow-lock issue does have a concrete analog in `IntentsBase.sol` / `ExtrinsicIntents.sol` in the Intent Gateway. However, the local variant is worse than a simple fund-lock: it is a **double-claim** driven by resolving the cross-chain module binding dynamically instead of pinning it at order-placement time.

### Title
Stale gateway re-binding via `NewDeployment` allows double-claim of escrowed order input (false "unfilled" proof) - (File: `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`IntentsBase._instance()` resolves the counterpart IntentGateway address for a state machine from the *current* `_instances` mapping, which is updated in place by `_addDeployment()` whenever Hyperbridge dispatches a `NewDeployment` message (e.g. a gateway migration/redeploy). Both cross-chain fill authentication (`_authenticate`) and cross-chain cancellation (`_cancelFromSource`) use this same live lookup rather than a value pinned to the order at placement time. If the destination-chain gateway address changes while an order is in flight, the source chain's view of "who is authoritative for this order" changes retroactively for orders that were already placed/filled against the old address.

### Finding Description
- `_addDeployment` (`evm/src/apps/intentsv2/IntentsBase.sol:521-524`) unconditionally overwrites `_instances[keccak256(chain)]`. [1](#0-0) 
- `_instance()` (`IntentsBase.sol:358-362`) always returns the current mapping value, with no per-order snapshot. [2](#0-1) 
- `_authenticate` (`ExtrinsicIntents.sol:63-67`) checks an incoming `RedeemEscrow`/`RefundEscrow` post request's `from` against `_instance(request.source)` — the *current* registered gateway, not the one live when the order was placed. [3](#0-2) 
- `_cancelFromSource` (`ExtrinsicIntents.sol:188-223`) builds the GET-request storage key against `_instance(order.destination)` — again the current registered address — to check whether `_filled[commitment]` is empty on the destination. [4](#0-3) 

Sequence:
1. User places a cross-chain order on chain A targeting chain B, whose registered gateway is `GatewayB_v1`.
2. A solver fills the order on `GatewayB_v1`, delivering output tokens to the beneficiary and dispatching a `RedeemEscrow` post request back to chain A (`from = GatewayB_v1`).
3. Before that message is relayed/accepted on chain A, Hyperbridge governance issues a legitimate `NewDeployment` for chain B pointing to `GatewayB_v2` (e.g. a routine migration). `_instances[B]` is now `GatewayB_v2`.
4. The `RedeemEscrow` message from `GatewayB_v1` arrives; `_authenticate` now compares `from == GatewayB_v1` against `_instance(B) == GatewayB_v2` and reverts with `Unauthorized`. The solver's legitimate redemption is permanently blocked — there is no retry path since the instance binding will never point back to `GatewayB_v1`.
5. After `order.deadline`, the user calls `_cancelFromSource`. The GET request is now built against `GatewayB_v2`'s storage slot for `_filled[commitment]`, which reads empty (the fill actually happened on `GatewayB_v1`, a different address/storage). `onGetResponse` treats this as proof the order was never filled and refunds the full escrowed input back to the user via `_withdraw`.

Net effect: the user receives the solver's output tokens (delivered in step 2) **and** the refunded input escrow (step 5), while the solver's `RedeemEscrow` is permanently rejected. The `_filled` storage-proof check that is supposed to be the single source of truth for "was this order settled" is defeated because it is evaluated against the wrong contract instance.

### Impact Explanation
This is a false-state-acceptance / duplicate-settlement bug: the GET-response handler accepts an "unfilled" proof that is actually just a proof about the wrong (new) contract's storage, not evidence the order was genuinely unfilled. It causes the protocol/solver to lose the escrowed input value while the user is paid twice for a single order — directly matching the bounty's "false proof/state acceptance" and "double-claim/double-settlement" categories, and constitutes real loss of funds without requiring any malicious relayer, prover, or peer; only a normal governance-driven gateway migration plus an in-flight order.

### Likelihood Explanation
Gateway/contract migrations are an expected, documented operational event (`NewDeployment`/`UpgradeContract` are first-class `RequestKind`s), and any order that is in flight (placed, possibly already filled) during such a migration window is exposed. No attacker privilege is needed beyond being an ordinary user/solver acting during this window; the race is between a routine admin action and an ordinary order lifecycle.

### Recommendation
Pin the counterpart gateway address into the order/commitment itself (or into per-order state) at placement time, and have both `_authenticate` and `_cancelFromSource`'s GET-key construction use that pinned address rather than a live re-resolution of `_instances`. Alternatively, gate `NewDeployment` updates so they cannot retroactively affect authentication for orders placed before the update, e.g. by keeping a history of valid instance addresses per chain and accepting messages from any address that was valid at the time the order was placed.

### Proof of Concept
1. On chain A, user places a cross-chain order for chain B while `_instances[B] = GatewayB_v1`.
2. On chain B, solver calls `fillOrder`, delivering output tokens and dispatching `RedeemEscrow` from `GatewayB_v1` (`ExtrinsicIntents.sol:89-171`). [5](#0-4) 
3. Before that message is delivered on chain A, simulate governance dispatching `NewDeployment` for chain B with `gateway = GatewayB_v2` (`onAccept` → `_addDeployment`). [6](#0-5) 
4. Deliver the `RedeemEscrow` message to chain A's `onAccept`; observe it reverts with `Unauthorized` from `_authenticate` because `_instance(B)` now resolves to `GatewayB_v2` (`ExtrinsicIntents.sol:63-67`).
5. After `order.deadline`, call `cancelOrder` → `_cancelFromSource`, whose GET request targets `GatewayB_v2`'s (empty) `_filled` slot for the commitment; feed back a GET response with an empty proof to `onGetResponse`, which calls `_withdraw(body, true, true)` and refunds the full input escrow to the user (`ExtrinsicIntents.sol:319-324`, `IntentsBase.sol:390-425`). [7](#0-6) [8](#0-7) 
6. Confirm the user's balance shows receipt of both the solver's output tokens (step 2) and the refunded input (step 5), while the solver's `RedeemEscrow` from step 2 can never succeed.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L358-362)
```text
    function _instance(bytes calldata stateMachineId) internal view returns (address) {
        address gateway = _instances[keccak256(stateMachineId)];
        if (gateway == address(0)) revert UnknownInstance();
        return gateway;
    }
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L521-524)
```text
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-171)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
        address beneficiary = address(uint160(uint256(order.output.beneficiary)));
        TokenInfo[] memory outputFills = new TokenInfo[](outputsLen);

        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

            if (solverAmount < totalRequired) revert InvalidInput();

            uint256 dust = solverAmount - totalRequired;
            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;

            if (dust > 0) {
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            }

            if (token == address(0)) {
                if (msgValue < solverAmount) revert InsufficientNativeToken();
                uint256 beneficiaryTotal = totalRequired + beneficiaryShare;
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, totalRequired + beneficiaryShare);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }
            if (protocolShare > 0) emit DustCollected(token, protocolShare);
            outputFills[i] = TokenInfo({token: outputToken, amount: totalRequired});
        }

        _execute(order, outputsLen);

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

        if (options.nativeDispatchFee > 0 && msgValue >= options.nativeDispatchFee) {
            IDispatcher(hostAddr).dispatch{value: options.nativeDispatchFee}(request);
            msgValue -= options.nativeDispatchFee;
        } else {
            dispatchWithFeeToken(request);
        }

        // Refund any unspent native tokens to the solver.
        if (msgValue > 0) {
            (bool sent,) = msg.sender.call{value: msgValue}("");
            if (!sent) revert InsufficientNativeToken();
        }

        emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: order.inputs});
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L202-223)
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```
