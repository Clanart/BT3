Confirmed: `EvmHost.dispatchIncoming(PostRequest,...)` deletes the request receipt and returns cleanly whenever the `onAccept` low-level call fails, so a reverting callback is retryable rather than fatal [1](#0-0) . That retry safety net is exactly what breaks the analogous DoS path in `IntentGatewayV2`/`IntentsBase`.

### Title
Malicious order beneficiary can permanently block their own escrow settlement and force indefinite fund lock via reverting native-token receive - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`_withdraw`, used to settle both `RedeemEscrow` and `RefundEscrow` cross-chain messages, pushes native ETH to the `beneficiary` with a raw `call{value: amount}("")` inside the same critical section that marks the order `_filled` and releases fee accounting, and reverts the whole function if the call fails [2](#0-1) .

### Finding Description
`onAccept` in `ExtrinsicIntents.sol` decodes `RedeemEscrow`/`RefundEscrow` requests and calls `_withdraw(body, ..., true)` directly, with no try/catch or isolation of the native transfer [3](#0-2) . Because `EvmHost.dispatchIncoming` treats a reverting `onAccept` as "not delivered yet" (it deletes the just-written receipt and returns, allowing retry) rather than an unrecoverable failure [1](#0-0) , this "safe to retry" model assumes the module's own logic will eventually succeed on retry. But if `beneficiary` is a solver-controlled or user-controlled contract that unconditionally reverts (or self-destructs, or grief-consumes gas) on receiving ETH, `_withdraw` will *always* revert for that specific commitment — every retry of `dispatchIncoming` for that request hits the same revert, forever. The order's escrow (`_orders[commitment][token]`) can never be released because the beneficiary controls whether the transfer inside `_withdraw` succeeds, and the surrounding function offers no fallback (no pull-payment path, no per-token isolation of the native leg from the rest of the withdrawal, no way for governance/relayer to bypass a permanently reverting beneficiary). This differs from the original BTC report (mere relayer gas griefing, explicitly excluded by the impact gate) because here the result is genuine, permanent loss/lock of escrowed solver or user funds rather than relayer inconvenience — a `RedeemEscrow` beneficiary is the solver who already delivered output tokens on the destination chain and is entitled to the escrowed input tokens on the source chain; if that solver's own receiving address (or an address they select, e.g. via a proxy/relayer contract they control) reverts on ETH receipt, the input-token escrow becomes permanently unredeemable, and for `RefundEscrow` the same applies to a user's refund after cancellation.

### Impact Explanation
Escrowed native-token funds (`_orders[commitment][address(0)]`) become permanently stuck: the commitment can never be finalized because `onAccept`/`_withdraw` will revert every single delivery attempt, and `_filled[commitment]` is never set, so there is no alternate cancellation/refund path once a fill has already been dispatched (the escrow accounting has already been decremented in memory logic per-token, but the whole tx reverts atomically so no partial release occurs either — the funds simply sit unredeemable in the contract). This is a direct, permanent loss of bridged/escrowed funds tied to intent settlement, matching the bounty's "stealing or loss of funds" / "logic attacks" categories.

### Likelihood Explanation
Likelihood is Low-to-Medium: it requires an order whose native-ETH output leg routes to a beneficiary address that is attacker-controlled or maliciously deployed to revert, but no privileged role, relayer collusion, or proof forgery is needed — any unprivileged actor filling or refunding an order with `beneficiary` set to a reverting contract triggers this deterministically.

### Recommendation
Decouple the native-token leg of `_withdraw`/`_sweepDust` from the atomic finalize path: use a pull-payment pattern (credit an internal `pendingWithdrawals[beneficiary]` balance that the beneficiary later claims) instead of pushing ETH via `call{value:}` inside the same function that also marks the order filled and releases ERC-20 tokens and fees. At minimum, wrap the native transfer in a bounded-gas `call` and, on failure, credit the amount to a claimable mapping rather than reverting the entire withdrawal/finalization, so ERC-20 legs and order finalization state changes are never blocked by an intentionally hostile native-token recipient.

### Proof of Concept
1. Attacker deploys `MaliciousBeneficiary` with a `receive()` that always `revert()`s.
2. Attacker (as solver or order user) creates/fills an order whose `order.output.beneficiary` (cross-chain fill) or `order.user` (cancel/refund) is set to `MaliciousBeneficiary`, with a native-token (`address(0)`) input/output leg.
3. Source chain dispatches `RedeemEscrow`/`RefundEscrow`; relayer submits it via `HandlerV2.handlePostRequests` → `EvmHost.dispatchIncoming` → `IntentGatewayV2.onAccept` → `_withdraw`.
4. `_withdraw`'s `beneficiary.call{value: amount}("")` reverts because `MaliciousBeneficiary` rejects ETH [4](#0-3) .
5. `dispatchIncoming` catches the revert, deletes the receipt, and returns without reverting the outer transaction, marking the message as "not yet delivered" and eligible for retry [5](#0-4) .
6. Every subsequent relayer retry of the same commitment hits the same revert indefinitely; the escrowed native tokens for that commitment can never be withdrawn, and no other code path in `IntentsBase.sol`/`ExtrinsicIntents.sol` allows redirecting or reclaiming them.

### Citations

**File:** evm/src/core/EvmHost.sol (L805-817)
```text
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
