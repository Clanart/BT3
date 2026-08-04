## Analog Found: IntentGatewayV2 same-chain fill uses the full per-token escrow balance instead of the per-leg entitlement

### Title
Same-chain fill releases the entire shared-token escrow balance instead of the fill's proportional share, draining escrow reserved for other unfilled order legs - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`_fillSameChain` computes the amount of escrowed input tokens to release to a solver on a full-fill branch by reading the *entire* current `_orders[commitment][token]` balance rather than the amount actually owed for that specific output leg. Because `_orders` is keyed only by `(commitment, token)` and not by leg index, this mirrors the reported Panoptic bug: an "available"/"withdrawable" quantity is computed from a pool balance that is shared with other still-outstanding obligations, without subtracting what is committed to those obligations.

### Finding Description
`_orders[bytes32 commitment][address token]` tracks escrowed input balances keyed only by `(commitment, token)`, aggregated across all input legs of the order that use the same token address: [1](#0-0) 

In `_fillSameChain`, when an output leg `i` reaches full fill (`amountFilled == totalRequired`), instead of releasing the proportional escrow amount reserved for leg `i`, the code releases whatever is currently sitting in the shared per-token bucket: [2](#0-1) 

If an order has two or more input/output leg pairs that happen to use the *same input token* (e.g. two output assets both paid for in USDC input), escrow for both legs accumulates into the single `_orders[commitment][USDC]` slot at placement time. When a solver fully fills the first such leg, `escrowedAmount = _orders[commitment][token]` pulls the *combined* balance — including the portion that was escrowed to back the second, still-unfilled leg — and `_withdraw` immediately decrements the shared slot by that same inflated amount: [3](#0-2) 

This is structurally identical to the Panoptic `maxWithdraw` flaw: an "available to release" quantity is derived from a pooled balance that does not subtract the portion earmarked for a different, still-live obligation (there: other short/long positions; here: another order leg). The result is that the first filler to complete their leg receives tokens that rightfully belong to the second leg's backing, and when the second leg is later filled, `_orders[commitment][token]` has already been zeroed or reduced below what's needed, so `_withdraw` reverts with `UnknownOrder` (leg permanently unfillable — locked funds for the second solver/beneficiary) or, if a nonzero leftover balance from other tokens exists, an incorrect amount is transferred.

### Impact Explanation
- A solver benefits from over-crediting on the fully-filled leg, receiving escrow that should remain reserved for a different, unfilled output pair — direct unauthorized transfer of value out of escrow to the wrong beneficiary/amount.
- The user/order owner suffers fund loss or a permanently stuck order: the second leg can never be correctly settled once the shared escrow bucket has been drained by the first leg's fill, matching the "temporarily/permanently locking buyers" impact class from the seed report.
- This is reachable by any unprivileged solver simply calling the public `fillOrder`/`_fillSameChain` path with a crafted or naturally-occurring order that has repeated input tokens across legs — no relayer, prover, or governance actor is required.

### Likelihood Explanation
Likelihood depends on whether typical order construction can produce multiple `TokenInfo` entries in `order.inputs` sharing the same token address while backing different `order.output.assets` entries. Nothing in `Order`/`TokenInfo` or `placeOrder` enforces token uniqueness across input legs, and the escrow accounting explicitly aggregates by token address only (not by index), so this condition is straightforwardly constructible by any user placing an order with repeated input token entries, then any solver filling legs in sequence.

### Recommendation
Track escrow per `(commitment, leg index)` instead of per `(commitment, token)`, or compute the full-fill release strictly as the pro-rata amount for that leg (`order.inputs[i].amount`) reduced by whatever has already been released for that specific leg, rather than reading the aggregate token-keyed balance. Add an invariant check that the sum of amounts released across all legs of a commitment never exceeds what was escrowed per leg at placement time.

### Proof of Concept
1. User places a same-chain order with two output legs, both requiring USDC as the input token: `order.inputs[0].token = USDC, amount = 1000`, `order.inputs[1].token = USDC, amount = 500`. At placement, `_orders[commitment][USDC] = 1500`.
2. Solver A fully fills output leg 0 (paying the exact `totalRequired` for output 0). In `_fillSameChain`, `amountFilled == totalRequired` triggers `escrowedAmount = _orders[commitment][USDC] = 1500` (the combined balance for both legs), not `1000`.
3. `_withdraw` transfers `1500` USDC to Solver A and sets `_orders[commitment][USDC] = 0`.
4. Solver B attempts to fill output leg 1 (which required 500 USDC of escrow backing). `_fillSameChain` computes `escrowedAmount = _orders[commitment][USDC] = 0`; `_withdraw` reverts with `UnknownOrder` on the input token, permanently blocking Solver B's fill and stranding the user's second output requirement — while Solver A has already extracted 500 USDC beyond their entitlement.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-140)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-123)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
```
