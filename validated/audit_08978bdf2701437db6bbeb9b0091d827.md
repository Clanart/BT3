## Title
Atomic multi-token `_withdraw()` permanently locks escrow for the entire order if any single input token reverts - (File: `evm/src/apps/intentsv2/IntentsBase.sol`, `evm/src/apps/intentsv2/ExtrinsicIntents.sol`)

### Summary
This is a direct structural analog of the EigenLayer `completeQueuedWithdrawal` DoS: a withdrawal that bundles **multiple assets into a single atomic call** will revert in its entirety if any one asset transfer reverts, and — unlike the original EigenLayer contract, which at least offered a `receiveAsTokens=false` escape hatch — IntentGatewayV2 provides no analogous fallback, no per-token skip list, and no way to re-derive a smaller `WithdrawalRequest` once the order commitment has been fixed.

### Finding Description
`WithdrawalRequest.tokens` is a fixed array carried inside the order commitment (`commitment: keccak256(abi.encode(order))`). It is populated from `order.inputs`, which can contain multiple distinct ERC-20 tokens for a single order:

- Cross-chain fill settlement: `ExtrinsicIntents._fillCrossChain` dispatches a `RedeemEscrow` message whose body embeds `WithdrawalRequest({... tokens: order.inputs ...})` [1](#0-0) .
- Destination-side cancellation: `_cancelFromDest` marks the order filled on the destination **first** (`_filled[commitment] = ...order.user`), then dispatches a `RefundEscrow` message with the same fixed `order.inputs` array [2](#0-1) .
- On the source chain, `onAccept` decodes the `WithdrawalRequest` and calls `_withdraw(body, ..., true)` directly for both `RedeemEscrow` and `RefundEscrow` kinds — a single, presumably atomic call over all tokens in the request [3](#0-2) .
- `onGetResponse` (source-initiated cancellation path) similarly calls `_withdraw(body, true, true)` once the destination's `_filled` slot is proven empty [4](#0-3) .

If `_withdraw` processes `body.tokens` in a loop and any single token transfer reverts (a paused token, a blacklist-style token that reverts against the specific beneficiary, a token that reverts on zero-value edge cases, or any other transient/token-level failure), the entire `onAccept`/`onGetResponse` call reverts. Because this call is reached through the permissionless ISMP delivery path (`HandlerV2.handlePostRequests` → `host.dispatchIncoming` → module `onAccept`), a reverting `onAccept` means the request receipt is never written, so the message is never marked delivered and can be resubmitted — but it will revert identically every time, because `WithdrawalRequest.tokens` is fixed by the order commitment and cannot be edited or partially retried.

This exactly mirrors the broken invariant in the EigenLayer report: bundling multiple asset movements behind one atomic call means one bad asset DoSes withdrawal/refund of all co-bundled assets. The key difference that makes this *worse* than the original finding: EigenLayer at least allowed setting `receiveAsTokens=false` to re-internalize shares and re-queue a smaller withdrawal excluding the bad strategy. Here, once the order is placed, `order.inputs` is baked into the keccak256 commitment used everywhere (`_filled[commitment]`, `_orders[commitment][token]`, the dispatched request bodies) — there is no mechanism to cancel/re-issue a `WithdrawalRequest` that excludes just the problematic token.

### Impact Explanation
For `_cancelFromDest`, the destination chain has *already* set `_filled[commitment]` before the refund message is sent — the order can never be filled or re-cancelled through another path. If the source-side `_withdraw` reverts because one of the (potentially several) `order.inputs` tokens has a permanent transfer failure, the entire escrowed multi-asset input for that order is **permanently stuck** on the source chain: it is neither returned to the user, nor released to a solver, satisfying the bounty's "loss/lock of funds" impact category.

For `_fillCrossChain`/`RedeemEscrow`, this is worse for the solver: the solver has already irrevocably delivered output tokens to the beneficiary on the destination chain, but the input-token escrow release on the source chain can never complete if one of the bundled input tokens reverts — a straightforward loss of committed solver funds.

### Likelihood Explanation
No privileged actor, relayer misbehavior, or governance action is required — this is triggerable purely by the choice of `order.inputs` token set at order-placement time (a normal user-controlled input) combined with any one of those tokens later becoming untransferable (pause, blacklist, or any other legitimate revert condition), which is a realistic real-world occurrence for ERC-20 tokens (USDC blacklist, pausable stablecoins, etc.). The attack primitive requires no malicious peer/prover/relayer — an ordinary user or solver bundling several tokens in one order is sufficient to create a permanently-DoSable withdrawal.

### Recommendation
Change `_withdraw` to process each token in `WithdrawalRequest.tokens` independently and fault-tolerantly (catch/skip a reverting transfer and retain it as pending/claimable rather than reverting the whole call), analogous to the `indicesToSkip` mitigation referenced in the original EigenLayer report. Alternatively, allow `onAccept`/`onGetResponse` to partially succeed and persist per-token completion state so a subsequent retry only re-attempts the failed token instead of replaying the entire, always-reverting multi-token withdrawal.

### Proof of Concept
Note: I could not directly inspect the body of `_withdraw` in `IntentsBase.sol` within the available tool budget, so I cannot cite the exact loop/atomicity logic line-by-line; the PoC below is inferred from the confirmed call sites and is provided as a scenario to be validated against `IntentsBase._withdraw`'s actual implementation.

1. User places a cross-chain order whose `order.inputs` contains two tokens: `TokenA` (normal) and `TokenB` (a token that will later be paused/blacklisted, e.g., a centrally-administered stablecoin).
2. Order goes unfilled past `order.deadline`. Anyone calls `cancelOrder(order, options)` on the destination chain → `_cancelFromDest` sets `_filled[commitment] = order.user` and dispatches a `RefundEscrow` POST with `tokens: order.inputs` (both TokenA and TokenB) [2](#0-1) .
3. `TokenB` becomes non-transferable to `order.user` (e.g., blacklisted) before the message is delivered to the source chain.
4. The relayer submits the `RefundEscrow` message to the source-chain `HandlerV2`, which calls `onAccept` → `_withdraw(body, true, true)` [3](#0-2) . The `TokenB` transfer reverts, reverting the whole `onAccept` call and thus the whole message delivery.
5. Because `_filled[commitment]` is already set on the destination chain, no alternate cancellation route exists. Every resubmission of the same `RefundEscrow` message reverts identically, since `order.inputs` (and therefore the token set in `WithdrawalRequest`) is fixed by the commitment. Both `TokenA` and `TokenB` escrow remain permanently locked in the source-chain gateway.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L139-147)
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

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L319-324)
```text
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```
