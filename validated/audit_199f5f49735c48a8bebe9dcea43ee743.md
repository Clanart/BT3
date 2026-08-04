### Title
Duplicate-token orders permanently brick same-chain cancellation via unsigned escrow underflow - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

### Summary
`IntrinsicIntents._cancelSameChain` builds one `remainingTokens[i]` entry per `order.inputs[i]` index and fills each entry with the *full current* per-token escrow balance read from `_orders[commitment][token]`. If an order's `inputs` array contains the same token address more than once, every duplicate index is populated with the same (full) remaining balance rather than a per-index share. `_withdraw` (`evm/src/apps/intentsv2/IntentsBase.sol:390-410`) then subtracts each entry's `amount` from the shared per-token mapping unconditionally. The second occurrence of the duplicated token subtracts an amount from a balance that has already been zeroed by the first occurrence, causing `escrowed - amount` to underflow (or hit the `escrowed == 0` revert guard) and revert the whole cancellation — permanently locking the user's own escrowed funds, structurally identical to the M-2 pattern of an unsigned per-key balance that cannot be legally driven through zero during a legitimate closing operation.

### Finding Description
`_cancelSameChain` (`evm/src/apps/intentsv2/IntrinsicIntents.sol:161-187`) is the only path used to reclaim escrow for a same-chain order: [1](#0-0) 

For each index `i` in `order.inputs`, it reads `_orders[commitment][token]` — the mapping keyed only by token address, not by input index — and stores that *entire remaining balance* into `remainingTokens[i]`. If `order.inputs` contains the same `token` twice (nothing in `placeOrder`/`Order` validation forbids this — inputs are just a caller-supplied array of `TokenInfo{token, amount}` pairs summed into the same mapping key at escrow time), both duplicate slots get the same full balance.

`_withdraw` then processes `body.tokens` sequentially: [2](#0-1) 

On the first occurrence of the duplicated token, `escrowed` (full balance) is transferred out and `_orders[commitment][token]` is set to `0`. On the second occurrence with the same token, `escrowed` is now `0`, and `if (escrowed == 0) revert UnknownOrder();` fires, aborting the entire transaction — including the transfers for *every other* token in the order that had already been queued in the same call. Because `_cancelSameChain`/`_withdraw` is executed as a single atomic call, this makes the cancellation unconditionally revert forever for that commitment: there is no other cancellation path for a same-chain order.

This mirrors the M-2 root cause exactly: an unsigned per-key balance (`_orders[commitment][token]`, analogous to `allocatedBalances`) is decremented by an amount computed from stale/duplicated state rather than the true remaining share, so a legitimate "close/net-out" action (`cancelOrder`) is guaranteed to underflow/revert and lock funds that the account is otherwise entitled to.

### Impact Explanation
The user's own escrowed input tokens for that order become permanently unrecoverable — `cancelOrder` is the sole self-chain exit path and it always reverts once triggered. Unlike the original M-2 report (which needed a specific price move to trigger insolvency), here the fund lock is deterministic and reproducible from order construction alone, with no dependency on price, relayers, or any third party. This satisfies the "loss/lock of funds" impact bucket in the required Hyperbridge impact gate.

### Likelihood Explanation
Likelihood is high once triggered but the precondition (duplicate token entries within a single order's `inputs`) must occur, either through a solver-generated malformed order, a buggy client integration, or a user deliberately splitting deposits of the same token into multiple `TokenInfo` entries for accounting/bookkeeping reasons (nothing in the contract prevents it, and nothing in the documented order semantics discourages it). No malicious peer, relayer, or admin is required — the caller triggering the loss is the same order owner who placed the order and calls `cancelOrder()` themselves.

### Recommendation
- In `placeOrder` (or a shared validation helper), reject orders whose `inputs` array contains duplicate token addresses, matching the invariant that `_orders[commitment][token]` is keyed uniquely per token.
- Alternatively, rewrite `_cancelSameChain` to build `remainingTokens` by iterating over the **unique set** of tokens actually present in `_orders[commitment]` (one entry per token, summed amount), rather than mirroring `order.inputs` index-for-index.
- Add a regression test placing a same-chain order with a repeated token in `inputs` and asserting `cancelOrder` successfully refunds the full escrowed amount rather than reverting.

### Proof of Concept
1. User calls `placeOrder` with `order.inputs = [ {token: USDC, amount: 500e6}, {token: USDC, amount: 500e6} ]` (same token twice, total 1000 USDC escrowed). `_orders[commitment][USDC]` is credited to `1000e6` (summed across both TokenInfo entries during escrow — token-keyed, not index-keyed).
2. Before any fill, the user calls `cancelOrder(order, ...)` → routes to `_cancelSameChain`.
3. Loop over `order.inputs` (2 entries, both USDC):
   - i=0: `escrowed = _orders[commitment][USDC] = 1000e6` → `remainingTokens[0] = {USDC, 1000e6}`
   - i=1: `escrowed = _orders[commitment][USDC] = 1000e6` (unchanged, read-only in this loop) → `remainingTokens[1] = {USDC, 1000e6}`
4. `_withdraw(body, true, true)` is called with `body.tokens = [{USDC,1000e6}, {USDC,1000e6}]`:
   - i=0: `escrowed = 1000e6`, transfers `1000e6` USDC out, sets `_orders[commitment][USDC] = 0`.
   - i=1: `escrowed = _orders[commitment][USDC] = 0` → `if (escrowed == 0) revert UnknownOrder();` — entire transaction reverts.
5. The cancellation can never succeed for this commitment; the user's 1000 USDC remains locked in the gateway indefinitely, with no alternate withdrawal path for a same-chain order. [1](#0-0) [3](#0-2)

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L169-186)
```text
        uint256 inputsLen = order.inputs.length;
        TokenInfo[] memory remainingTokens = new TokenInfo[](inputsLen);
        bool hasEscrow = false;
        for (uint256 i; i < inputsLen;) {
            address token = address(uint160(uint256(order.inputs[i].token)));
            uint256 escrowed = _orders[commitment][token];
            if (escrowed > 0) hasEscrow = true;
            remainingTokens[i] = TokenInfo({token: order.inputs[i].token, amount: escrowed});
            unchecked {
                ++i;
            }
        }
        if (!hasEscrow) revert UnknownOrder();

        WithdrawalRequest memory body =
            WithdrawalRequest({commitment: commitment, tokens: remainingTokens, beneficiary: order.user});

        _withdraw(body, true, true);
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
