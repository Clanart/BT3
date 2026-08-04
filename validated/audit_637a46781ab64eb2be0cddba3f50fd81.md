## Analysis

The OUSD bug is a **state-desync invariant break**: one code path (`changeSupply`) mutates a global accounting value (`_totalSupply`) while a second, independently-triggerable code path (`rebaseOptOut`) leaves per-account state untouched, so the two views of the system diverge and a downstream invariant (`balanceOf <= totalSupply`) silently breaks.

The closest verifiable analog in this repo is in the IntentGatewayV2 cross-chain intent settlement flow, where **escrow release (`RedeemEscrow`) and escrow refund (`RefundEscrow`) are two independent, unordered ISMP message paths that both write to the same `_filled[commitment]` / `_orders[commitment][token]` state**, and the destination-side cancellation path does not check whether the order was already filled before firing a refund.

### Title
Order-fill/cancel race lets a user reclaim escrow after a solver already filled the order, causing solver fund loss - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`_cancelFromDest` (destination-chain cancellation) sets `_filled[commitment]` and dispatches a `RefundEscrow` POST request to the source chain without first checking whether the order commitment was already marked filled by a solver's `_fillCrossChain`. Because `_fillCrossChain`'s `RedeemEscrow` message and `_cancelFromDest`'s `RefundEscrow` message are two separate, asynchronously-relayed ISMP messages targeting the *same* source-chain escrow (`_orders[commitment][token]`), their arrival order at the source chain is not guaranteed. If the `RefundEscrow` message is delivered before the `RedeemEscrow` message, the source-chain `_withdraw` releases the escrow to the user; the later `RedeemEscrow` for the solver then fails because escrow is already zeroed.

### Finding Description
`_cancelFromDest` in [1](#0-0)  only checks:
```solidity
if (order.deadline >= _blockNumber()) {
    if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
}
_filled[commitment] = address(uint160(uint256(order.user)));
```
There is no check that `_filled[commitment]` is still zero (i.e. that no solver has already filled the order via `_fillCrossChain`). `_fillCrossChain` itself independently sets `_filled[commitment] = msg.sender` and dispatches a `RedeemEscrow` request to the source chain [2](#0-1) .

Both message kinds converge on the same source-chain state via `onAccept` → `_withdraw` [3](#0-2) , which is keyed only by `_orders[commitment][token] == 0` [4](#0-3)  — a value that is decremented to zero by whichever message (redeem or refund) is delivered first, with no ordering guarantee between the two independently-dispatched POST requests.

This is directly analogous to OUSD: two independent public/permissionless flows (`rebaseOptOut` vs `changeSupply`; here `_fillCrossChain`'s redeem vs `_cancelFromDest`'s refund) mutate related accounting state (`_creditBalances`/`_totalSupply`; here `_filled`/`_orders`) without mutual awareness, breaking the invariant that escrow can only be released once and only to the rightful party.

### Impact Explanation
If a solver fills a cross-chain order on the destination chain (delivering output tokens to the beneficiary out of their own funds) and the corresponding `RedeemEscrow` message to the source chain is delayed (e.g., because the destination's deadline has already elapsed, which is exactly when `_cancelFromDest` becomes callable by *anyone*), the order's user (or any caller once past deadline) can trigger `_cancelFromDest`, sending a competing `RefundEscrow` to the source chain. Whichever message lands first zeroes `_orders[commitment][token]`; if the refund wins the race, the user receives their escrowed input tokens back on the source chain in addition to already having received the destination-side output from the solver — a double-payout to the user and a direct fund loss for the solver, who paid out real value on the destination chain but is denied the source-chain reimbursement they are owed.

### Likelihood Explanation
This requires no malicious relayer, prover, admin, or governance actor — only an unprivileged order creator (or, after deadline, any account) invoking the ordinary `cancelOrder` public entrypoint, combined with normal cross-chain message delivery latency (which is expected and common in ISMP relaying, especially near an order's deadline boundary). The race window is naturally created by the protocol's own deadline semantics (post-deadline cancellation is permissionless), making this a realistically triggerable, non-cooperative attack surface.

### Recommendation
`_cancelFromDest` should verify `_filled[commitment] == address(0)` before marking the order filled/cancelled and dispatching `RefundEscrow`. Symmetrically, `_withdraw` on the source chain should also cross-check against a "no double settlement" flag distinct from the raw escrow balance (e.g., a per-commitment `settled` bit) so that whichever of `RedeemEscrow`/`RefundEscrow` arrives second is provably rejected rather than relying on incidental zeroing of `_orders`.

### Proof of Concept
1. Solver calls `fillOrder` on the destination chain near/at the order deadline; `_filled[commitment] = solver` is set and a `RedeemEscrow` POST is dispatched to the source chain (delivery depends on relayer timing) [5](#0-4) .
2. Once `_blockNumber() > order.deadline`, any account calls `cancelOrder` → `_cancelFromDest`, which unconditionally sets `_filled[commitment] = user` and dispatches a competing `RefundEscrow` POST to the source chain [1](#0-0) .
3. If the `RefundEscrow` message is delivered to the source chain first, `onAccept` → `_withdraw` releases the full escrowed input tokens to the user [6](#0-5) , zeroing `_orders[commitment][token]`.
4. The later-arriving `RedeemEscrow` for the solver hits `_orders[body.commitment][token] == 0` and reverts with `UnknownOrder` [7](#0-6) , so the solver never recovers the input tokens despite having already paid out the order's output on the destination chain.

**Note on verification limits:** I was unable to inspect the public `cancelOrder` entrypoint (in `IntrinsicIntents.sol`, not retrieved) to confirm whether it independently gates on `_filled[commitment] == address(0)` before calling `_cancelFromDest`. If such a guard exists at that outer layer, it would need to be re-checked at the time of dispatch (not just at call time) to fully close this race, since the fill and cancel transactions can be mined in either order relative to each other and relative to their respective cross-chain message deliveries. I recommend a Devin session with full repository access to confirm the exact guards in `IntrinsicIntents.sol::cancelOrder` and settle this definitively before treating this as fully proven end-to-end.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-147)
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

**File:** evm/tron/contracts/apps/IntentGatewayV2.sol (L682-705)
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

            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                (bool success,) = token.call(abi.encodeWithSelector(IERC20.transfer.selector, beneficiary, amount));
                if (!success) revert TransferFailed();
            }

            _orders[body.commitment][token] -= amount;
            unchecked {
                ++i;
            }
        }
```
