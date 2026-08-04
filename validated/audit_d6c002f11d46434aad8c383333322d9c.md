## Analysis

The RocketPool bug pattern — an irrevocable state transition ("kick") that unconditionally succeeds while the compensating fund movement (returning the bond) can permanently fail and block/strand funds — has a direct analog in Hyperbridge's `IntentGatewayV2` cross-chain cancellation flow.

### Title
Destination-side order cancellation is marked irrevocable before the cross-chain escrow refund is guaranteed to succeed, permanently locking user funds if the refund transfer fails - ([File: evm/src/apps/intentsv2/ExtrinsicIntents.sol])

### Summary
`_cancelFromDest` permanently commits `_filled[commitment] = order.user` — blocking the order from ever being filled again — in the *same* transaction that merely *dispatches* a `RefundEscrow` message to the source chain. The actual return of escrowed tokens happens later and separately, inside `_withdraw` on the source chain. If that later `_withdraw` call reverts (e.g. the input token permanently reverts transfers to `order.user`, such as a blacklist/pausable ERC20), the destination-side cancellation can never be undone, yet the escrowed principal on the source chain can never be released either — there is no admin/governance path that can sweep or redirect it.

### Finding Description
`_cancelFromDest` [1](#0-0)  marks the order as finalized (`_filled[commitment] = address(uint160(uint256(order.user)))`) and dispatches a `RefundEscrow` POST request to the source chain, all before any proof that the refund will actually complete. This mirrors `_fillCrossChain`'s pattern of marking `_filled` before dispatch [2](#0-1) , but for cancellation this "finalize now, settle later" split is one-directional and has no compensating path.

When the `RefundEscrow` message eventually arrives on the source chain, `onAccept` routes it to `_withdraw` [3](#0-2) , which performs the actual token transfer: [4](#0-3) 

If the ERC20 `safeTransfer` (or the native-token `.call`) reverts — for example because the token has since blacklisted or paused the beneficiary — the entire `_withdraw` call reverts. At the host level, `dispatchIncoming` treats this as a retryable failure and deletes the request receipt so it "can be retried": [5](#0-4) 

But if the underlying condition (blacklist/pause on the beneficiary) is durable, every retry fails identically. Meanwhile, on the destination chain, `_filled[commitment]` is already permanent — the order can never be filled, and `cancelOrder` on that commitment is a dead end (`Filled()` on any further attempt) [6](#0-5) . There is no governance/admin rescue for this specific ledger: the only sweep entrypoint, `SweepDust`, operates on protocol dust accumulated via `TRANSACTION_FEES`/surplus, not on a specific order's `_orders[commitment][token]` escrow balance [7](#0-6) .

This is structurally identical to the RocketPool bug: the irrevocable "kick" (here, destination-side order finalization) is decoupled from — and can outlive — the fund-return step (here, source-side `_withdraw`), and unlike the RocketPool fix (skip/continue the bond return so the kick isn't blocked), here there is no fallback at all: the escrow is neither returned nor recoverable.

### Impact Explanation
This results in a permanent, unrecoverable loss/lock of the user's escrowed input tokens: the order can never be filled (destination already finalized it) and can never be refunded (source-side transfer permanently reverts), with no admin path to recover the specific commitment's escrow. This matches the bounty's "stealing or loss of funds" / "fund loss/lock" impact category, since bridged/escrowed assets fail to "move exactly once and only to the rightful beneficiary."

### Likelihood Explanation
This is reachable by any unprivileged actor: the order's own user (or, after the deadline, any third party) can call `cancelOrder` from the destination chain to trigger `_cancelFromDest`. The only precondition is that the input token can permanently refuse a transfer to the beneficiary address at redemption time (e.g., a centrally-blacklistable stablecoin used as escrow input, or the user's own address becoming sanctioned/paused between order placement and refund) — a realistic condition for the exact token families (USDC/USDT-style) intent gateways are built to support, not a contrived edge case requiring a malicious relayer, prover, or admin.

### Recommendation
Decouple destination-side finalization from the guarantee of a successful refund, following the same fix pattern used for the RocketPool report: make the escrow-release step resilient to per-token transfer failures (e.g., wrap each token transfer in `_withdraw` with a try/catch or low-level call, and on failure credit the amount to a per-beneficiary claimable balance instead of reverting the whole withdrawal), or provide a governance-callable rescue path keyed by `(commitment, token)` so escrow that cannot be delivered directly is not stranded forever.

### Proof of Concept
1. User places a cross-chain order on chain A with `order.inputs` denominated in a blacklist-capable ERC20 (e.g., USDC-like token), destined for chain B.
2. Before the order is filled, `order.user`'s address gets blacklisted by the token issuer (or the user's key is later associated with a sanctioned address) — a scenario outside the protocol's control but realistic for such tokens.
3. Anyone calls `cancelOrder` from chain B after the deadline; `_cancelFromDest` sets `_filled[commitment] = order.user` and dispatches `RefundEscrow` to chain A (`evm/src/apps/intentsv2/ExtrinsicIntents.sol:240-259`).
4. On chain A, `onAccept` → `_withdraw` attempts `IERC20(token).safeTransfer(beneficiary, amount)` to the blacklisted `order.user` and reverts every time (`evm/src/apps/intentsv2/IntentsBase.sol:404-409`).
5. `EvmHost.dispatchIncoming` deletes the receipt each time to allow a retry (`evm/src/core/EvmHost.sol:812-816`), but retries are futile since the blacklist condition persists.
6. Result: the order is permanently `Filled` on chain B (cannot be re-cancelled or filled), and the escrowed tokens are permanently stuck in the `IntentGatewayV2` contract on chain A with no sweep/rescue mechanism for that specific commitment.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-94)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

        uint256 msgValue = msg.value;
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-259)
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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L579-597)
```text
    function _sweepDust(SweepDust memory req) internal {
        uint256 outputsLen = req.outputs.length;
        for (uint256 i; i < outputsLen;) {
            TokenInfo memory info = req.outputs[i];
            address token = address(uint160(uint256(info.token)));
            uint256 amount = info.amount;

            if (token == address(0)) {
                (bool sent,) = req.beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(req.beneficiary, amount);
            }
            unchecked {
                ++i;
            }
            emit DustSwept(token, amount, req.beneficiary);
        }
    }
```

**File:** evm/src/core/EvmHost.sol (L794-818)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
    }
```

**File:** evm/src/apps/IntentGatewayV2.sol (L470-491)
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
}
```
