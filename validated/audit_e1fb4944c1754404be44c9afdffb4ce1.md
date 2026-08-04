Confirmed: `_orders` is declared as `mapping(bytes32 => mapping(address => uint256)) public _orders;` — keyed only by `(commitment, tokenAddress)`, not by output-pair index. [1](#0-0) 

### Title
Same-chain partial-fill final-fill branch drains the aggregate per-token escrow bucket, allowing a solver to steal escrow reserved for a sibling output leg that shares an input token address - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
This is the local structural analog of the `ETHCrowdfundBase._processContribution` bug class: a boundary/rounding condition in per-unit accounting (`escrowedAmount` vs. a single aggregate balance) lets an unprivileged actor force an under-accounted state to release more (or the wrong) funds than the accounting model intends, with no privileged actor, relayer, or prover involved.

### Finding Description
`_fillSameChain` computes, per output-asset index `i`, whether the leg is now fully filled and, if so, releases the escrow **for that token address** via a single aggregate lookup instead of a per-index tracked amount: [2](#0-1) 

```solidity
if (totalRequired > amountFilled) isFullyFilled = false;
...
uint256 escrowedAmount;
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
```

`_orders[commitment][token]` is a single balance keyed by `(commitment, token address)` only — it is not scoped per output index/leg: [1](#0-0) 

If an order's `inputs` array contains two (or more) entries that reference the **same input token address** at different indices (nothing in `placeOrder`/`_fillSameChain` rejects duplicate input token addresses across indices), the escrow for both legs is pooled into one bucket. When the output pair at index `i` is the *first* of the two legs to reach `amountFilled == totalRequired` for its own output token, the "final fill" branch does not calculate a proportional share of the pooled bucket — it hands the **entire remaining balance of that token in the bucket** to whichever solver completed that leg, via `_withdraw`: [3](#0-2) 

That includes escrow that was placed by the user to pay for the *other*, still-outstanding output leg. The second leg's solver, upon later completing their leg, finds `_orders[commitment][token]` already decremented below what their leg is entitled to, and either receives a truncated/zero amount or the withdrawal underflows/reverts (since `_orders[commitment][token] = escrowed - amount` would revert on underflow, `UnknownOrder()` on `escrowed == 0`), permanently blocking their otherwise-valid fill.

This mirrors the crowdfund bug's core defect exactly: an aggregate/global counter (`_orders[commitment][token]`, analogous to `totalContributions`) is checked and drained by a per-leg/per-contribution boundary condition (`amountFilled == totalRequired`, analogous to `newTotalContributions >= maxTotalContributions_`) without accounting for the fact that the aggregate represents multiple logically-separate obligations. In the crowdfund case this caused rounding to zero and a DoS; here it causes fund misallocation — an unprivileged solver captures escrow that belongs to a co-existing leg.

### Impact Explanation
- Wrong beneficiary/amount: a solver on one leg receives tokens escrowed for a different leg of the same order.
- Loss/lock of funds: the solver for the other leg is unable to withdraw their entitled input tokens (underflow revert or reduced payout), and the user's escrow accounting no longer matches what was placed.
- No privileged actor, malicious relayer, or prover is required — any two ordinary solver addresses filling different legs of the same order trigger this, and the "first to complete a leg" solver benefits at the expense of the other. This fits the bounty's "false state acceptance / wrong beneficiary or amount / fund loss" categories on a same-chain, purely EVM-contract, public entrypoint (`fillOrder`).

### Likelihood Explanation
Requires the order to have ≥2 `inputs` entries sharing the same token address (e.g., a user requesting two independent output legs both funded from the same input token, which nothing in the visible order-construction/validation path forbids), and requires two different solvers (or the same solver in two transactions) to race to complete each leg. This is a realistic multi-leg order shape and does not require any relayer/prover/admin collusion — only ordinary solver participation in a race that any observer of the mempool could trigger deliberately.

### Recommendation
Track escrow per `(commitment, outputAssetIndex)` rather than aggregated only by `(commitment, tokenAddress)`, or explicitly disallow multiple `order.inputs` entries with the same token address at `placeOrder` validation time. The "final fill releases full remaining balance" fix (Finding #4) should only ever consume the escrow slice specifically associated with the leg being completed, never the full aggregate bucket for that token address.

### Proof of Concept
Not independently executed in this session — the analysis is derived from static reading of `_fillSameChain` (`evm/src/apps/intentsv2/IntrinsicIntents.sol`) and the `_orders`/`_withdraw` accounting in `evm/src/apps/intentsv2/IntentsBase.sol`. I was unable to locate and fully verify `placeOrder`'s input-validation code in this session's tool budget to confirm whether duplicate input-token addresses across `order.inputs` are explicitly rejected; if `placeOrder` does reject duplicate input token addresses, this specific analog does not apply and would need re-scoping. I recommend a Devin session to (a) confirm whether `placeOrder` allows two `inputs[]` entries with an identical token address, and (b) if so, write a Foundry test reproducing two output legs sharing one input token, where solver A completes leg 1 first and drains the full `_orders[commitment][token]` balance, leaving solver B's completion of leg 2 to revert with `UnknownOrder()` or underflow.

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L113-123)
```text
            if (totalRequired > amountFilled) isFullyFilled = false;
            if (protocolShare > 0) emit DustCollected(token, protocolShare);

            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
            outputFills[i] = TokenInfo({token: outputToken, amount: fillAmount});
```
