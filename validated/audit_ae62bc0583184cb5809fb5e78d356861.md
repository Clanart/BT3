### Title
Escrow over-release in same-chain partial fills when multiple order legs share the same token - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

### Summary
`_fillSameChain` releases escrow for a completed output leg by reading the *entire* remaining balance stored in `_orders[commitment][token]` instead of the proportional amount owed for that specific leg. This is safe only when each input/output token pair maps 1:1 in the order. When an order has multiple output legs that resolve to the same output token (and/or multiple input legs that resolve to the same input token), completing the *smallest* of those legs triggers release of the *combined* escrow balance for the whole token — exactly the same class of bug as the Across `handleAcrossMessage` report: code that treats a value scoped to one step (`amount`/`fillAmount` for a single leg) as if it represented the entire outstanding total, and short-circuits the “done” logic without validating that assumption is correct in general.

### Finding Description
In `_fillSameChain` (`evm/src/apps/intentsv2/IntrinsicIntents.sol:54-149`), per-leg partial-fill accounting is tracked in `_partialFills[commitment][outputToken]`, keyed only by **token address**, not by output index: [1](#0-0) 

When a leg becomes fully paid (`amountFilled == totalRequired`), the escrow released for that leg is computed as: [2](#0-1) 

```solidity
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
```

`_orders[commitment][token]` is a running balance keyed purely by token address (confirmed in `IntentsBase.sol` / used identically in `ExtrinsicIntents.sol` and `IntrinsicIntents.sol`), and it accumulates escrow from **every** input entry that resolves to that same token address, regardless of which output index it is paired with. The “full leg release” branch was added to fix rounding dust for the *single-pair, sequential multi-solver* case (see `testPartialFill_RoundingDustReleasedToFinalSolver`), where it is correct because the whole balance for that token *is* the remainder of that one pair.

That assumption breaks for an order whose `output.assets` (or `inputs`) contains more than one entry using the **same token address** at different indices. `fillOrder`/`_fillSameChain` only checks array-length equality between `order.inputs` and `order.output.assets` (`IntentGatewayV2.sol:438-440`) and never enforces per-index token uniqueness: [3](#0-2) 

So a solver who pays only the *smallest* leg's requirement in full will trigger `amountFilled == totalRequired` for that index, and the code hands them the **entire aggregated escrow balance** for that token — including the value backing the other, still-unpaid, legs.

### Impact Explanation
This is a direct, unauthorized-transfer / fund-theft bug reachable by an ordinary, unprivileged solver — no malicious relayer, prover, or admin is required. A solver can pay a fraction of the total required output value for an order and drain the full escrowed balance of a shared input token, at the direct expense of the order owner. It satisfies the bounty's "stealing or loss of funds" and "logic attacks / incorrect transfer amount" categories, matching the exact bug-class from the seed report: code that assumes a locally-observed “amount” fully represents an outstanding total, without checking that other, decoupled state (other legs sharing the token) hasn't already accounted for part of that balance.

### Likelihood Explanation
Requires the order to contain more than one input or output entry sharing the same token address at different array indices — a structure not explicitly disallowed anywhere in `placeOrder`/`fillOrder`/`_fillSameChain`. Multi-asset orders (bundled swaps, multi-leg payouts, or split escrow structuring) are a natural and expected usage pattern for the Intent Gateway's array-based `Order.inputs`/`Order.output.assets`, so this is not a contrived edge case — any wallet/integrator that builds multi-leg orders without being aware of this pitfall is exposed to any solver monitoring the mempool/order book.

### Recommendation
Track and release escrow per **output-leg index**, not per aggregated token balance. Either:
- Key `_partialFills` and the "final leg" balance check by `(commitment, outputIndex)` in addition to token, and release exactly `order.inputs[i].amount` proportional to that leg's own contribution rather than reading the shared `_orders[commitment][token]` total, or
- Reject orders at `placeOrder`/`fillOrder` time whose `output.assets` (or `inputs`) contain duplicate token addresses across indices, restoring the 1:1 pairing the "release full remaining balance" optimization assumes.

### Proof of Concept
1. User places an order with:
   - `inputs = [{token: USDC, amount: 1000}, {token: USDC, amount: 1000}]`
   - `output.assets = [{token: DAI, amount: 1000}, {token: DAI, amount: 1000}]`
   - Total escrow: 2000 USDC recorded under `_orders[commitment][USDC] = 2000`.
2. Malicious solver calls `fillOrder` with `options.outputs = [{DAI, 1000}, {DAI, 0}]`.
3. In `_fillSameChain`, iteration `i = 0`: `alreadyFilled = 0`, `totalRequired = 1000`, `solverAmount = 1000` → `fillAmount = 1000`, `amountFilled = 1000 == totalRequired` → `escrowedAmount = _orders[commitment][USDC] = 2000` (the *entire* combined escrow, not the 1000 owed for this leg).
4. Iteration `i = 1`: `alreadyFilled = _partialFills[commitment][DAI] = 1000`, `remaining = 1000 - 1000 = 0` → branch is skipped (`continue`), `isFullyFilled` remains `true` because `solverAmount == 0` triggers the `continue` path without ever being flagged as incomplete for a `remaining == 0` leg.
5. `_withdraw` is called with `finalize = true`, transferring the solver the full 2000 USDC in exchange for only 1000 DAI paid to the beneficiary — the second DAI leg is never paid, and the order is marked filled, permanently losing the user's 1000 USDC.

Note: I was not able to fully verify, due to tool/iteration limits, whether `placeOrder` (in `IntentGatewayV2.sol`) performs any implicit deduplication or validation on `order.output.assets`/`order.inputs` token addresses beyond what was inspected in `_fillSameChain`/`fillOrder`; a Devin session with full file access should confirm the complete `placeOrder` validation logic before finalizing remediation.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L74-99)
```text
            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
            uint256 fillAmount;

            uint256 beneficiaryShare = 0;
            uint256 protocolShare = 0;
            if (alreadyFilled == 0 && solverAmount > totalRequired) {
                fillAmount = totalRequired;
                uint256 dust = solverAmount - totalRequired;
                if (order.output.call.length > 0) {
                    protocolShare = dust;
                } else {
                    protocolShare = (dust * _params.surplusShareBps) / 10_000;
                    beneficiaryShare = dust - protocolShare;
                }
            } else {
                fillAmount = solverAmount > remaining ? remaining : solverAmount;
            }

            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
            uint256 beneficiaryTotal = fillAmount + beneficiaryShare;
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

**File:** evm/src/apps/IntentGatewayV2.sol (L438-446)
```text
        uint256 outputsLen = order.output.assets.length;
        if (options.outputs.length != outputsLen) revert InvalidInput();
        if (order.inputs.length != outputsLen) revert InvalidInput();

        if (isSameChain) {
            _fillSameChain(order, options, commitment);
        } else {
            _fillCrossChain(order, options, commitment);
        }
```
