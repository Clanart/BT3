Confirmed: `placeOrder` (`evm/src/apps/IntentGatewayV2.sol:162`) allows the order creator to freely choose any ERC20 contract address for `order.inputs[]` with no allowlist check, and `_withdraw` (`evm/src/apps/intentsv2/IntentsBase.sol:390`) later releases all of those tokens in a single all-or-nothing loop.

### Title
All-or-nothing multi-token escrow release lets one adversarial input token permanently lock a solver's entire redemption - (File: evm/src/apps/intentsv2/IntentsBase.sol)

### Summary
`IntentsBase::_withdraw` iterates over every token in `WithdrawalRequest.tokens` and calls `IERC20.safeTransfer` for each, exactly the "loop over independent claims, one bad item blocks all" pattern from the report (`Karma::_slash` looping over `IRewardDistributor` and reverting entirely if one distributor is paused). Here the equivalent "distributor" is an arbitrary ERC20 the order creator picked as one of the order's `inputs`.

### Finding Description
`placeOrder` (`evm/src/apps/IntentGatewayV2.sol:162-383`) lets any unprivileged user construct `order.inputs` from arbitrary token addresses — there is no allowlist or validation beyond `amount != 0` and no-duplicates (`IntentGatewayV2.sol:163,233,283,337`). A user can escrow a mix of legitimate high-value tokens (e.g. USDC) alongside a token they fully control whose `transfer`/`transferFrom` behaves normally on the way in (during escrow) but is made to always revert on the way out (e.g. a token that reverts on transfers to a specific contract, or one that becomes blacklisted/paused after escrow but before redemption).

When a solver fills the order cross-chain, `_fillCrossChain` (`ExtrinsicIntents.sol:89-171`) dispatches a `RedeemEscrow` request back to the source chain carrying the full `order.inputs` array. On the source chain, `onAccept` (`ExtrinsicIntents.sol:289-295`) calls `_withdraw(body, false, true)`, which loops:
```solidity
for (uint256 i; i < len; i++) {
    ...
    IERC20(token).safeTransfer(beneficiary, amount);   // IntentsBase.sol:408
}
```
Since Solidity reverts unwind the whole call, a single reverting token transfer aborts the release of **every** token in the array — including the legitimate, valuable ones — not just the bad one. The same code path (`_withdraw`) is also used for `RefundEscrow` (cancel flows), so cancellation is equally blocked.

This mirrors the report's core broken invariant: a batched claim/redemption process is implemented as strictly all-or-nothing over a set of independently-controlled items, so one poisoned item denies the entire process — but here the "poison" is fully attacker-selectable at order-creation time (no admin, relayer, or prover involved), unlike the original bug which needed a paused (admin-controlled) distributor.

### Impact Explanation
The solver has already delivered real output value to the beneficiary on the destination chain (`_fillCrossChain`, lines 121-135) before the redeem message is even dispatched. If the source-side `_withdraw` can never succeed because of one adversarial input token, the solver's payment (all tokens in `order.inputs`, including the legitimate ones) is permanently stuck in the `IntentGatewayV2` contract — the escrow can be neither released to the solver nor refunded to the user (both paths run through the same `_withdraw`). This is a direct, attacker-triggerable loss/lock of funds belonging to an honest counterparty (the solver), fitting the bounty's "stealing or loss of funds" / "logic attacks" categories, and requires no compromised relayer, prover, or admin — only an unprivileged order creator.

### Likelihood Explanation
High for a motivated attacker: crafting a token with normal inbound `transferFrom` behavior but reverting outbound `transfer` (or one that can be selectively frozen/paused post-escrow, e.g., a custom token or an existing token with owner-controlled blacklisting) is straightforward and entirely within the order creator's control. No race condition, timing, or privileged role is needed.

### Recommendation
Make `_withdraw` resilient to per-token failures instead of all-or-nothing: wrap each `safeTransfer` in a try/catch (or use raw `call` and check success without reverting the loop), skip/queue failed transfers for later retry or a separate sweep, and only mark the order `_filled`/finalize once independent of individual transfer outcomes — analogous to the report's fix of adding an `isPaused()` check and skipping paused distributors rather than reverting the whole slash.

### Proof of Concept
1. Attacker deploys `EvilToken`, an ERC20 whose `transfer` succeeds unconditionally except reverts when `msg.sender == <the known IntentGateway address>` (or simply reverts on `transfer` while `transferFrom` into the gateway works fine).
2. Attacker calls `placeOrder` with `order.inputs = [ {token: USDC, amount: 10_000e6}, {token: EvilToken, amount: 1} ]`, `order.output` set for a cross-chain destination, escrowing both tokens via `IntentGatewayV2.sol:281-298`.
3. A solver calls `fillOrder` → `_fillCrossChain` on the destination chain, transferring the requested output tokens to the beneficiary (`ExtrinsicIntents.sol:121-132`) and dispatching `RedeemEscrow` back to the source chain.
4. On the source chain, `onAccept` → `_withdraw` (`IntentsBase.sol:390-410`) processes `body.tokens = [USDC, EvilToken]`; the `EvilToken.transfer` call reverts, unwinding the entire transaction — the solver receives **none** of the escrowed USDC despite having paid the output tokens.
5. Every retry of the message delivery hits the same revert; `_filled[commitment]` is never set, so the 10,000 USDC (and the solver's expected payment) is permanently stuck in `IntentGatewayV2`, and `cancelOrder`'s `RefundEscrow` path is equally blocked since it reuses `_withdraw`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** evm/src/apps/IntentGatewayV2.sol (L162-163)
```text
    function placeOrder(Order memory order, bytes32 graffiti) public payable nonReentrant {
        if (order.inputs.length == 0) revert InvalidInput();
```

**File:** evm/src/apps/IntentGatewayV2.sol (L333-343)
```text
        // Phase 3: Credit escrow.
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            // Reject duplicate input tokens
            if (_orders[commitment][token] != 0) revert InvalidInput();
            _orders[commitment][token] = reducedInputs[i].amount;

            unchecked {
                ++i;
            }
        }
```
