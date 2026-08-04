Based on my investigation, I found a genuine local analog of the H-3 rounding/unfair-distribution bug class in Hyperbridge's same-chain intent-filling logic.

### Title
Floor-division rounding in partial-fill escrow release lets whichever solver completes an order sweep dust owed to earlier partial fillers - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`_fillSameChain` supports permissionless partial filling of an order by *any* solver across multiple transactions. Each non-final partial fill computes the solver's proportional escrow release with floor division, `escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired` [1](#0-0) , which systematically under-pays every non-final filler by up to `totalRequired - 1` wei of rounding dust per input token. The filler who happens to submit the fill that finally satisfies `amountFilled == totalRequired` instead takes the *entire remaining* escrow balance, `escrowedAmount = _orders[commitment][...]` [2](#0-1) , sweeping up all the dust that was shorted from every earlier partial filler.

### Finding Description
An order can be partially filled by any address because `_filled[commitment]` is reset to zero after a non-final fill: `delete _filled[commitment];` [3](#0-2) . This makes the remaining-amount fill (`_vote`-equivalent step) open to a different, unrelated solver in a later transaction — there is no exclusivity or "first solver only" restriction.

For every partial fill that does not complete the order, the amount of input tokens released to the filler is computed with integer division: `escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;` [4](#0-3) . This truncates any fractional remainder, so the filler receives strictly less than their exact proportional share; the shorted amount stays locked in the `_orders[commitment][token]` escrow mapping [5](#0-4) .

When a subsequent fill (by any solver) brings `amountFilled == totalRequired`, the code switches branches and releases the *whole remaining escrow balance* rather than the proportional share for that specific fill: `escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];` [2](#0-1) . This branch was presumably intended only to clean up any single dust wei belonging to *that same filler's own* prior partial fills, but nothing in the logic restricts the "completing" fill to being submitted by the solver who filled the earlier increments. Any solver who is fast enough (or colludes/waits) to submit the last increment collects the accumulated rounding remainder that rightfully should have gone, proportionally, to whichever solvers filled the earlier increments.

This is structurally the same broken invariant as the Velocimeter `poke()` report: a proportional-distribution formula that uses floor division across multiple independent participants, where the final participant in the sequence receives value that should have been split among all participants, resulting in unfair/incorrect distribution of escrowed funds. The `_withdraw` function does not guard against this — it merely `continue`s on zero-amount releases [6](#0-5) , silently absorbing the shorted dust into the escrow balance rather than reverting or accounting for it per-filler.

### Impact Explanation
This directly causes wrong-beneficiary/wrong-amount fund distribution among permissionless, unprivileged solvers competing to fill the same order — no relayer, prover, admin, or governance actor is required. An attacker acting as a solver can deliberately structure a `fillOrder` call with a `solverAmount` chosen to maximize the floor-division loss on an earlier partial fill (of themselves or another solver), then ensure they are the one to submit the final completing fill for that order, capturing escrow dust that was contractually owed to the earlier fillers. Across many orders/tokens this can be repeated to systematically skim from other competing solvers.

### Likelihood Explanation
The path requires only calling the existing public `fillOrder` entrypoint (which routes into `_fillSameChain`) multiple times with attacker-chosen `FillOptions.outputs[i].amount` values for the same order commitment — an entirely permissionless, unprivileged action available to any solver. The only timing dependency is being either of the two (or more) fillers of a given order, which is realistic in a competitive, permissionless solver market that this exact contract is designed to support.

### Recommendation
Track escrow released per-filler-per-increment based on exact proportional math (e.g., carry forward the rounding remainder explicitly and add it only to the same filler's future completions, or use a cumulative "amount that should have been released so far" calculation — `released = orderAmount * amountFilled / totalRequired` computed fresh each time and diffed against the previously released cumulative amount — so the *increment* released is always correct rather than assuming a single filler owns all prior dust).

### Proof of Concept
1. Solver A calls `fillOrder` with `solverAmount` for output token `i` chosen so that `(order.inputs[i].amount * fillAmount) / totalRequired` truncates, e.g. `order.inputs[i].amount = 100`, `totalRequired = 3`, `fillAmount = 1` → `escrowedAmount = 33` (mathematically deserved: `33.33`, actual fair share truncated by `0.33`).
2. Solver A receives `33` input tokens for paying `1/3` of the output; `_orders[commitment][token]` still holds `67` (should conceptually hold `66.67` for the remaining `2/3`).
3. Solver B (a different, unrelated address) calls `fillOrder` with `solverAmount = 2` (the remaining), completing the order: `amountFilled == totalRequired` triggers the full-balance branch, releasing all `67` tokens to Solver B instead of the fair `66.67`.
4. Solver B has captured `0.33` extra tokens that belonged, proportionally, to Solver A's fill — repeatable and scalable across orders with adversarially chosen amounts/decimals to make truncation loss larger relative to `totalRequired`.

**Note on verification limits**: I was not able to fully read the outer `fillOrder`/`onAccept` entrypoint in `IntentGatewayV2.sol` within the available iterations to confirm the exact reentrancy guard wording around `_filled[commitment]`, though the reset-to-zero behavior after a partial fill (`IntrinsicIntents.sol` line 140) combined with the unconditional entry-time set (line 57) is sufficient to establish that separate transactions from different solvers can each independently trigger `_fillSameChain` for the same commitment while it remains partially filled.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-121)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L139-141)
```text
        } else {
            delete _filled[commitment];
            emit PartialFill({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L136-141)
```text
    /**
     * @dev Maps (commitment, token address) to the escrowed amount for that token.
     * Decremented as tokens are released via fills or refunds.
     */
    mapping(bytes32 => mapping(address => uint256)) public _orders;

```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L394-403)
```text
        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
```
