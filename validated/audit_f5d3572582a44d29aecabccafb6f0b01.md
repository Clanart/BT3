## Title
`_cancelFromDest` allows re-cancelling an already-filled order, causing the legitimate solver's escrow release to be permanently blocked / double-dispatch of conflicting settlement messages - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`fillOrder()` in `evm/src/apps/IntentGatewayV2.sol` explicitly guards against re-processing a finalized order with `if (_filled[commitment] != address(0)) revert Filled();` [1](#0-0) . The cross-chain cancellation path `_cancelFromDest()` in `evm/src/apps/intentsv2/ExtrinsicIntents.sol` never performs this same "already finalized" check before overwriting `_filled[commitment]` and dispatching a `RefundEscrow` message to the source chain [2](#0-1) . This mirrors the reported marketplace bug class exactly: one code path (`fillOrder`) validates finalization state, a sibling path that produces an equivalent state transition (`_cancelFromDest`) omits the same check.

### Finding Description
`_cancelFromDest` only checks the deadline and, when before the deadline, the caller identity:
```solidity
function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
    if (order.deadline >= _blockNumber()) {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
    }
    _filled[commitment] = address(uint160(uint256(order.user)));
    ...
    // dispatch RefundEscrow to source chain
}
``` [2](#0-1) 

It never checks `_filled[commitment] != address(0)`, unlike `fillOrder`:
```solidity
function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
    if (order.deadline < _blockNumber()) revert Expired();
    ...
    if (_filled[commitment] != address(0)) revert Filled();
    ...
}
``` [1](#0-0) 

Because after the deadline "anyone may trigger" `cancelOrder` on the destination chain (per the documented design) [3](#0-2) , an attacker can call `cancelOrder()`/`_cancelFromDest()` on the destination chain immediately after the deadline **even though a solver already legitimately filled the order via `fillOrder()`** before the deadline (which sets `_filled[commitment] = solver` and dispatches a `RedeemEscrow` message to the source chain via `_fillCrossChain`) [4](#0-3) . `_cancelFromDest` overwrites `_filled[commitment]` with the user and dispatches a competing `RefundEscrow` message to the same source-chain commitment.

On the source chain, `onAccept()` processes both `RedeemEscrow` and `RefundEscrow` messages identically through `_withdraw(body, isRefund, true)` [5](#0-4) . `_withdraw` zeroes the per-token escrow (`_orders[commitment][token]`) on first successful processing and reverts with `UnknownOrder()` on any subsequent attempt for the same commitment/token [6](#0-5) . Whichever message (the solver's `RedeemEscrow` or the attacker-triggered `RefundEscrow`) is relayed/executed first on the source chain wins; the second one reverts. If the `RefundEscrow` message wins the race, the escrowed input tokens are sent back to the user instead of the solver — even though the solver already delivered the full output tokens to the beneficiary on the destination chain in good faith. If the `RedeemEscrow` message wins, the attacker's `RefundEscrow` simply reverts (harmless), but the destination-side `_filled[commitment]` was still corrupted to point at the user rather than the solver, and a spurious message/relayer fee was spent.

### Impact Explanation
This breaks the "bridged assets/order escrow must move exactly once and only to the rightful beneficiary" invariant. An unprivileged third party (no relayer/prover/admin assumptions needed) can trigger a state transition on the destination chain that, once relayed through the existing ISMP messaging pipeline (which offers no additional gate here — `onAccept` trusts any `RedeemEscrow`/`RefundEscrow` message authenticated only by gateway instance identity), can redirect escrowed input tokens away from a solver who already performed real, valuable work (delivering the requested output tokens) toward the original user. This is a wrong-beneficiary fund movement / fund loss for solvers, triggered purely by public entrypoint calls (`fillOrder` + `cancelOrder`) with no privileged actor involved.

### Likelihood Explanation
The precondition — calling `cancelOrder()` right at/after `order.deadline` on the destination chain — is explicitly permitted to "anyone" by design (no authorization needed once expired), and a solver's legitimate fill can occur in the same block window right before expiry. Any observer watching for fills near a deadline can race a cancel call against the solver's cross-chain settlement message. This does not require a malicious relayer, prover, or governance actor — only a public call to an already-permissionless function.

### Recommendation
Add the same finalization guard used in `fillOrder` to `_cancelFromDest` (and audit `_cancelFromSource`/`_cancelSameChain` for the same gap):
```solidity
function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
    if (_filled[commitment] != address(0)) revert Filled();
    if (order.deadline >= _blockNumber()) {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
    }
    ...
}
```
This prevents cancellation from being dispatched for an order that has already been finalized by a fill.

### Proof of Concept
1. User places a cross-chain order on chain A with `deadline = D`.
2. At block `D` (or just before), a solver calls `fillOrder(order, options)` on destination chain B; `_fillCrossChain` sets `_filled[commitment] = solver` and dispatches `RedeemEscrow` toward chain A [4](#0-3) .
3. Immediately after block `D+1`, any address (attacker) calls `cancelOrder(order, options)` on chain B, hitting `_cancelFromDest`, which does not check `_filled[commitment]` and proceeds to overwrite it and dispatch `RefundEscrow` toward chain A [2](#0-1) .
4. If the `RefundEscrow` message is relayed/executed on chain A before the solver's `RedeemEscrow` message, `onAccept` -> `_withdraw` sends the escrowed input tokens back to the user and zeroes `_orders[commitment][token]` [7](#0-6) .
5. The solver's later `RedeemEscrow` message reverts with `UnknownOrder()` — the solver, having already paid out the output tokens to the beneficiary in step 2, never receives the escrowed input tokens: a direct fund loss caused solely by an unprivileged public call.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L413-426)
```text
    function fillOrder(Order calldata order, FillOptions calldata options) public payable nonReentrant {
        if (order.deadline < _blockNumber()) revert Expired();
        bytes32 commitment = keccak256(abi.encode(order));

        address hostAddr = host();
        bytes32 currentChain = keccak256(IDispatcher(hostAddr).host());
        bytes32 orderSource = keccak256(order.source);
        bytes32 orderDest = keccak256(order.destination);
        bool isSameChain = orderSource == orderDest;

        if (isSameChain && orderSource != currentChain) revert WrongChain();
        if (!isSameChain && orderDest != currentChain) revert WrongChain();

        if (_filled[commitment] != address(0)) revert Filled();
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-267)
```text
    function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
        if (order.deadline >= _blockNumber()) {
            if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
        }

        _filled[commitment] = address(uint160(uint256(order.user)));

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

        address hostAddr = host();
        if (msg.value > 0) {
            IDispatcher(hostAddr).dispatch{value: msg.value}(request);
        } else {
            dispatchWithFeeToken(request);
        }
    }
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

**File:** docs/content/developers/evm/intent-gateway/overview.mdx (L67-67)
```text
**Cancel from destination chain**: Before the deadline, only the order owner can call `cancelOrder()` on the destination chain. After the deadline, anyone can cancel. The function marks the order as cancelled locally (`_filled[commitment] = user`) to prevent future fills, then dispatches a cross-chain `RefundEscrow` message back to the source chain. When the source chain receives this message via `onAccept()`, it calls `withdraw()` to refund the escrowed tokens to the user and emits `EscrowRefunded(commitment)`.
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
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
```
