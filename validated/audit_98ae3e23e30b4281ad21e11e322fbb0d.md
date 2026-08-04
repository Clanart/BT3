### Title
Griefer-controlled relayer fee in `_cancelFromDest` permanently desynchronizes cross-chain order state, locking escrowed funds - (File: `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
`_cancelFromDest` finalizes order state on the destination chain immediately and irreversibly, then dispatches a `RefundEscrow` POST request back to the source chain to actually release the escrowed funds. The relayer fee and payer for that dispatch are taken directly from the caller's `options.relayerFee` / `msg.sender`/`msg.value`, exactly the pattern flagged in the source report (Connext `xcall` with attacker-controlled `msg.sender`/`msg.value`). After the order deadline, this function is intentionally callable by anyone, so any unprivileged address can trigger the irreversible local finalization while supplying an unviable (e.g. zero) relayer fee for the cross-chain leg, so the message is never economically worth relaying to the source chain.

### Finding Description
`_cancelFromDest` (`evm/src/apps/intentsv2/ExtrinsicIntents.sol:240-267`):
```solidity
function _cancelFromDest(Order calldata order, CancelOptions calldata options, bytes32 commitment) internal {
    if (order.deadline >= _blockNumber()) {
        if (order.user != bytes32(uint256(uint160(msg.sender)))) revert Unauthorized();
    }

    _filled[commitment] = address(uint160(uint256(order.user)));   // <-- irreversible local finalization

    bytes memory body = bytes.concat(
        bytes1(uint8(RequestKind.RefundEscrow)),
        abi.encode(WithdrawalRequest({commitment: commitment, tokens: order.inputs, beneficiary: order.user}))
    );

    DispatchPost memory request = DispatchPost({
        dest: order.source,
        to: abi.encodePacked(_instance(order.source)),
        body: body,
        timeout: 0,
        fee: options.relayerFee,   // <-- attacker-controlled fee
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

Two facts combine to create the analog of the reported bug class:
1. `_filled[commitment]` is set on the destination chain *before* the cross-chain message that actually authorizes the release of funds on the source chain is even guaranteed to reach its destination. This mirrors `MainVault`/`Game`'s pattern of irreversibly mutating local state and *then* firing off a cross-chain call whose delivery is not guaranteed.
2. The caller of `_cancelFromDest` (any address once `order.deadline` has passed — see the explicit "anyone may trigger" comment) fully controls `options.relayerFee`, which is passed straight through as `DispatchPost.fee`. Hyperbridge relayers are only economically incentivized to deliver a POST request if the fee is sufficient; `dispatch()`/`dispatchWithFeeToken()` in `EvmHost.sol` accept `post.fee == 0` without any minimum-fee enforcement (`evm/src/core/EvmHost.sol:921-948`).

Because `timeout: 0` is also hard-coded, the request will never time out and thus will never trigger `PostRequestTimeoutHandled` fee-refund logic — the message simply sits undelivered indefinitely if no relayer relays a 0-fee request.

### Impact Explanation
An unprivileged caller (any address, once `order.deadline` has passed — by design not required to be the order owner or a solver) can call the public entry point wrapping `_cancelFromDest` with `options.relayerFee = 0` and no native `msg.value`/fee-token approval sufficient to cover a real relayer incentive. This:
- Permanently marks the order as finalized (`_filled[commitment]`) on the destination chain, blocking any legitimate solver from ever filling it there.
- Never actually reaches the source chain gateway with a fee attractive enough for a relayer to deliver the `RefundEscrow` message, so the input tokens escrowed on the source chain (`_orders[commitment][token]`) are never released via `_withdraw`.

The result is exactly the cross-chain desynchronization from the external report: destination state says "cancelled/finalized", source chain never receives the authorization to release funds, and the user's original escrowed inputs are stuck (loss of funds / permanent lock) with no automatic remediation, since `timeout: 0` also disables the timeout-refund safety net.

### Likelihood Explanation
Reachable by any address after `order.deadline` with no special privileges, funds, relayer collusion, or governance access — matching the "unauthorized/unprivileged execution causing loss/lock of funds" bar. The only requirement is passing a low/zero `relayerFee`, which is fully within caller control and not validated anywhere in `_cancelFromDest`, `dispatch()`, or `dispatchWithFeeToken()`.

### Recommendation
- Do not finalize destination-side state (`_filled[commitment]`) until the source-chain-authorizing message is confirmed deliverable, or make the finalization reversible/retryable if the cross-chain dispatch fee proves insufficient.
- Enforce a minimum relayer fee (or require the caller to fully self-relay by reverting if `fee == 0` and no timeout is set), and/or set a non-zero `timeout` for `RefundEscrow`/cancellation dispatches so that a stuck message can eventually be retried or refunded rather than being silently unrecoverable.
- Consider decoupling "who may trigger cancellation" from "who controls the incentive/fee of the resulting cross-chain settlement message," e.g. by using a protocol-configured minimum fee rather than trusting the caller's `options.relayerFee`.

### Proof of Concept
1. User places a cross-chain order with `order.deadline = D`; input tokens are escrowed on the source chain, and the corresponding gateway instance is registered on the destination chain.
2. Order is never filled by a solver.
3. Once `_blockNumber() > D`, an unrelated attacker (not the order owner, not a solver) calls the public function that wraps `_cancelFromDest(order, options, commitment)` with `options.relayerFee = 0` and `msg.value = 0` (or an amount that only covers minimal dispatch overhead but not a real relayer incentive).
4. `_filled[commitment]` is immediately set on the destination chain, permanently blocking any future fill of that order.
5. `dispatchWithFeeToken(request)` is called with `fee = 0`; the POST request commitment is recorded by `EvmHost.dispatch` (`_requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: 0})`), but with `timeout: 0` the message can sit indefinitely with no economic incentive for any relayer to submit the proof.
6. The source-chain gateway never executes `RefundEscrow` via `onAccept`, so `_withdraw` is never invoked there; the user's escrowed input tokens (`_orders[commitment][token]`) remain locked forever, while the destination chain believes the order is already finalized. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** evm/src/core/EvmHost.sol (L921-948)
```text
    function dispatch(DispatchPost memory post) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                post.fee, path, address(this), block.timestamp
            );
        } else if (post.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), post.fee);
        }

        // adjust the timeout
        uint64 timeoutTimestamp = post.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(post.timeout);
        PostRequest memory request = PostRequest({
            source: host(),
            dest: post.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            to: post.to,
            timeoutTimestamp: timeoutTimestamp,
            body: post.body
        });

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
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
