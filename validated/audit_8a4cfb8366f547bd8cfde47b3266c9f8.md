### Title
Cross-chain intent settlement race lets a post-deadline cancellation steal escrow already earned by a filling solver — ([File: evm/src/apps/intentsv2/ExtrinsicIntents.sol])

### Summary
### Finding Description
This is the same broken invariant as the raffle report: a single-slot state variable (`_filled[commitment]`) represents mutually-exclusive terminal states of an order (filled-by-solver vs. cancelled/refunded-to-user), and more than one code path writes it unconditionally without checking whether a *different, already-value-bearing* state was previously written.

On the destination chain, `_fillCrossChain()` pays the solver's output tokens to the beneficiary immediately and then unconditionally stamps `_filled[commitment] = msg.sender` before dispatching a `RedeemEscrow` message back to the source chain: [1](#0-0) 

Separately, `_cancelFromDest()` — callable by *anyone* once `order.deadline` has passed — also unconditionally stamps `_filled[commitment] = order.user` and dispatches a competing `RefundEscrow` message to the source chain, with no check that the order was already filled: [2](#0-1) 

Both cross-chain messages (`RedeemEscrow` and `RefundEscrow`) are handled on the *source* chain by the same `_withdraw()` function, which likewise overwrites `_filled[commitment]` to whichever beneficiary is named in the message currently being processed, with **no check of the prior `_filled` value** — the only thing gating a double-payout is whether escrow for that commitment is still non-zero: [3](#0-2) 

Because neither the destination-side stamp-and-dispatch calls nor the source-side settlement call cross-check `_filled` against the *other* possible terminal path, whichever of `RedeemEscrow` / `RefundEscrow` is delivered and processed first on the source chain wins the entire escrow balance for that commitment — determined purely by cross-chain message delivery order, not by which action actually happened first or is legitimate.

### Impact Explanation
If a solver fills a cross-chain order right at/after `order.deadline` (delivering real output tokens to the user's beneficiary on the destination chain) and, before the resulting `RedeemEscrow` message lands on the source chain, anyone (the docs explicitly allow "a keeper or relayer" or the user themselves) calls `cancelOrder` from the destination chain, a competing `RefundEscrow` message is also fired at the source chain. Whichever message is delivered/processed first drains the entire source-side escrow to its named beneficiary. If `RefundEscrow` wins the race, the order's `user` receives back their escrow after already having received the solver's output payment — the solver, having paid real value on the destination chain, permanently loses the source-side reimbursement it was entitled to (the competing `RedeemEscrow` reverts once it lands, because `_orders[commitment][token]` is already zero). This is a fund-loss / wrong-beneficiary outcome reachable by an ordinary, unprivileged order participant — no malicious relayer, prover, or admin is required, only normal permissionless cross-chain message delivery timing.

### Likelihood Explanation
The race window is real and requires only public entrypoints (`fillOrder`, `cancelOrder`) plus normal ISMP relaying, which is inherently asynchronous with no delivery-order guarantee between the two independently dispatched requests. Whether the top-level `fillOrder`/`cancelOrder` wrapper functions add an additional `_filled != 0` guard before invoking `_fillCrossChain`/`_cancelFromDest` could not be fully confirmed in this pass (those wrapper implementations were not located within the available context), so the exact width of the race window on the destination side is not fully certain. However, the source-side settlement function `_withdraw()` — which is unambiguously confirmed — has no such guard at all, meaning even if the destination side is fully serialized, the fundamental problem (whichever cross-chain message lands first at the source wins the escrow, regardless of legitimacy) still stands whenever both dispatches can be produced (e.g., fill happens right before/at deadline while cancellation is simultaneously permitted).

### Recommendation
- In `_withdraw()` (`IntentsBase.sol`), require `_filled[body.commitment] == address(0)` before finalizing, and revert with `Filled()`/`Cancelled()` if a terminal state was already recorded — do not rely solely on the escrow balance to gate a second settlement attempt.
- In `_fillCrossChain()` and `_cancelFromDest()` (`ExtrinsicIntents.sol`), check the current value of `_filled[commitment]` immediately before writing and dispatching, rejecting the action if the order is already filled or already cancelled, closing the destination-side race window as well.
- Consider making the deadline-based "anyone can cancel" path additionally check an authoritative on-chain/off-chain signal that the order was not filled (similar to the source-side `_cancelFromSource` GET-proof pattern) rather than relying purely on local, racy state.

### Proof of Concept
1. Solver calls `fillOrder` on the destination chain at/after `order.deadline`, paying the user's beneficiary in full; `_filled[commitment]` is set to the solver, and a `RedeemEscrow` message is dispatched toward the source chain (`ExtrinsicIntents.sol:89-93`).
2. Before that message is relayed and processed, anyone (permitted post-deadline) calls `cancelOrder` on the destination chain; `_filled[commitment]` is overwritten to `order.user` and a `RefundEscrow` message is dispatched toward the source chain (`ExtrinsicIntents.sol:240-250`).
3. If the `RefundEscrow` message is delivered/processed on the source chain first, `_withdraw()` drains the full escrow to `order.user` and sets `_filled[commitment] = user` (`IntentsBase.sol:390-410`), with no check that a `RedeemEscrow` for the same commitment was already earned/in flight.
4. The later-arriving `RedeemEscrow` for the solver reverts (`escrowed == 0` in `_withdraw`), permanently denying the solver the escrow it was owed for output tokens it already delivered on the destination chain.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L89-93)
```text
    function _fillCrossChain(Order calldata order, FillOptions calldata options, bytes32 commitment) internal {
        uint256 outputsLen = order.output.assets.length;

        _filled[commitment] = msg.sender;

```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L240-250)
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
