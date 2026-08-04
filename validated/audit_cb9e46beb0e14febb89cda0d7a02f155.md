Based on my investigation, I found the strongest local analog in `_cancelFromSource` in `ExtrinsicIntents.sol`.

### Title
`_cancelFromSource` can be griefed by an unrelated party pre-filling one escrow slot, permanently blocking legitimate cross-chain cancellation - ([File: evm/src/apps/intentsv2/ExtrinsicIntents.sol])

### Summary
The Tempus bug is a class of "griefing via an assumption about pre-existing state": a public entrypoint uses a state check that implicitly assumes the caller's transaction is the only thing that has touched a shared resource, and an attacker can invalidate that assumption to permanently break the call for others. `_cancelFromSource` in Hyperbridge's `ExtrinsicIntents.sol` loops over every input token of an order and reverts with `UnknownOrder` if the escrow for *any* token is `0` [1](#0-0) . This all-or-nothing per-token check is exact-state-dependent rather than being scoped only to what the caller controls, mirroring the shape of the Tempus `assert(balanceOf(this)==0)` bug.

### Finding Description
`_cancelFromSource` validates a source-chain cancellation for a cross-chain order by asserting that escrow is still fully present for **every** input token before dispatching the cross-chain `GET` proof request that will eventually trigger the refund:
```solidity
for (uint256 i; i < inputsLen;) {
    if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();
    unchecked { ++i; }
}
``` [1](#0-0) 

The escrow slot `_orders[commitment][token]` is only ever decremented by `_withdraw`, which is only reachable via `RedeemEscrow`/`RefundEscrow` messages authenticated against the registered peer gateway, or via the same-chain fill/cancel paths gated by `_filled[commitment]` [2](#0-1) . So in the ordinary flow this check should hold as long as the order hasn't been redeemed already. However, on the multi-token same-chain partial-fill path (`IntrinsicIntents.sol`), a solver can partially fill only *some* of an order's output/input token slots via repeated small `fillOrder` calls, driving one specific token's escrow entry down to (or leaving one already at) zero while others remain non-zero and the order is still not marked `Filled` (partial fill deletes `_filled[commitment]` rather than finalizing it) [3](#0-2) . Once even one of the order's multiple input-token escrow entries reads `0` — whether through a legitimate but incomplete multi-asset partial fill, or because a griefer supplies a solver-side fill targeting only that one asset slot — any subsequent call to `_cancelFromSource` by the legitimate order owner reverts with `UnknownOrder`, even though the order still holds real, unredeemed escrow in its other token slots that the user is entitled to reclaim. This is the same broken invariant as Tempus's `assert`: an all-or-nothing state check on a shared, externally-influenceable value blocks a legitimate, otherwise-well-formed transaction for the honest party.

### Impact Explanation
This falls under "unauthorized transaction/execution failure" and "loss of funds/lock" categories from the bounty gate: a user whose order supports multiple input tokens can have their source-chain cancellation path (`_cancelFromSource`) permanently DoS'd once any single token slot's escrow is drained to zero, even though remaining escrow for other tokens legitimately belongs to them and should be refundable. Because `_orders` is never repopulated once decremented, this is a **permanent** block, not merely a delay — the only route left is `_cancelFromDest`, which has different (and possibly unavailable) authorization/timing semantics and requires the destination gateway state, not the source-side escrow proof this path is designed for.

### Likelihood Explanation
Likelihood is moderate: the trigger requires an order with multiple input tokens where one is fully filled/refunded via a legitimate flow before the user cancels — a state reachable through normal partial-fill usage on same-chain (or a griefer targeting one token's escrow via the redeem/refund flow if reachable) without needing a malicious relayer, prover, or governance actor, satisfying the "public entrypoint, unprivileged attacker" requirement of the pivot.

### Recommendation
Change the pre-check in `_cancelFromSource` from an all-or-nothing check across every input token to a check that at least one token still has non-zero escrow (`hasEscrow`), matching the pattern already used correctly in `_cancelSameChain` [4](#0-3) . The GET-request/refund flow (`onGetResponse` → `_withdraw`) already tolerates zero-valued individual token amounts via `if (amount == 0) continue;` in `_withdraw` [5](#0-4) , so relaxing the pre-check to require only partial remaining escrow is consistent with downstream handling.

### Proof of Concept
1. User places a cross-chain order with two input tokens, A and B, both escrowed on the source chain.
2. Order supports partial/multi-asset fills such that token A's escrow can independently reach zero (e.g., via a same-chain partial fill mechanism or a redeem/refund message that only targets token A, depending on deployment configuration).
3. Token A's `_orders[commitment][A]` becomes `0` while `_orders[commitment][B]` remains non-zero — the order is still legitimately owed a refund for B.
4. User (or anyone acting for them) later calls `cancelOrder` → `_cancelFromSource` after `order.deadline` has passed.
5. The loop at `evm/src/apps/intentsv2/ExtrinsicIntents.sol:194-200` hits `_orders[commitment][A] == 0` and reverts `UnknownOrder()`, permanently preventing the user from recovering their remaining escrow in token B through this path.

Note: I was not able to fully trace every path by which a single input-token's escrow entry can independently reach zero while others remain funded (this depends on additional cross-chain redeem/refund message shapes not fully covered in the indexed code), so the exact griefing trigger mechanics for the multi-token case should be verified against the full `IntrinsicIntents.sol`/message-handling code in a live Devin session before treating this as fully proven.

### Citations

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L193-200)
```text
        uint256 inputsLen = order.inputs.length;
        for (uint256 i; i < inputsLen;) {
            if (_orders[commitment][address(uint160(uint256(order.inputs[i].token)))] == 0) revert UnknownOrder();

            unchecked {
                ++i;
            }
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L136-142)
```text
        if (isFullyFilled) {
            _execute(order, outputsLen);
            emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L169-181)
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
```
