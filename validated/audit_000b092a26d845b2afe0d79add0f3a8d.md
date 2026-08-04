## Title
Duplicate output-token legs in `IntentGatewayV2` partial-fill loop permanently lock escrowed input funds and skip delivery - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

## Summary
`_fillSameChain` iterates `order.output.assets` by index and tracks per-leg fill progress in `_partialFills[commitment][outputToken]`, keyed only by the output **token address**, not by array index. `placeOrder` validates and rejects duplicate **input** tokens (`evm/src/apps/IntentGatewayV2.sol:336-337`: `if (_orders[commitment][token] != 0) revert InvalidInput();`), but there is no equivalent check preventing duplicate **output** tokens in `order.output.assets`. This mirrors the Balancer report's root cause: a shared/aliased accumulator combined with unguarded subtraction produces either a revert or, worse, an incorrect "already satisfied" result.

## Finding Description
In `_fillSameChain` (`evm/src/apps/intentsv2/IntrinsicIntents.sol:66-124`):

```solidity
uint256 alreadyFilled = _partialFills[commitment][outputToken];
uint256 remaining = totalRequired - alreadyFilled;
if (remaining == 0 || solverAmount == 0) {
    if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
    continue;
}
``` [1](#0-0) 

If `order.output.assets` contains two entries with the **same token address** but different `amount`s (e.g. leg *i* requires 100 of token X, leg *j* requires 50 of token X), a normal fill of leg *i* to completion sets `_partialFills[commitment][X] = 100`. When the loop reaches leg *j* in the *same transaction*, `alreadyFilled` is read as 100:
- If `totalRequired[j] < alreadyFilled` → `remaining` underflows and the whole call panics/reverts (Solidity 0.8 checked arithmetic) — the direct underflow analog of the Balancer bug.
- If `totalRequired[j] == alreadyFilled` → `remaining == 0`, so the code `continue`s, leaving `isFullyFilled` **unchanged (still true)** for leg *j*, `outputFills[j]` and `escrowedInputs[j]` at their zero-initialized defaults, and `_partialFills[commitment][X]` untouched.

Because `escrowedInputs[j].amount == 0`, `_withdraw` skips the transfer for that entry entirely (`if (amount == 0) continue;`) [2](#0-1)  — so leg *j*'s dedicated escrowed input token (`order.inputs[j]`, guaranteed unique per the duplicate-input-token check) is never released. Yet because `isFullyFilled` never gets set to `false` for this leg, the overall fill is finalized as complete: `_filled[commitment] = msg.sender` is set at the top of the function [3](#0-2) , and on the `isFullyFilled` branch the order is finalized and `OrderFilled` is emitted [4](#0-3) .

Once `_filled[commitment]` is non-zero the order is permanently finalized — same-chain cancellation (`_cancelSameChain`) is the only recovery path and it does not check `_filled` before computing remaining escrow via `_orders[commitment][token]` for `order.inputs`, but a full walk of that path was not completed before the iteration budget ran out; what is confirmed is that `order.inputs[j]`'s escrow balance in `_orders[commitment][token_j]` is never decremented and never transferred to anyone once the order is marked filled through this path, leaving those funds stranded in the contract under a `commitment` that has already been marked complete.

## Impact Explanation
This produces either:
1. **Denial of service via arithmetic panic** — any attempt by a solver to fill an order containing duplicate output-token legs with certain amount orderings reverts unconditionally (the direct local analog of the cited Balancer underflow bug), or
2. **Locked funds** — the escrowed input tokens backing the skipped leg are never delivered to any party (not the solver, not swept back to the user), and the order is marked finalized (`_filled[commitment]` set) so the normal fill path cannot be retried for that leg.

This is reachable by an ordinary, unprivileged order-placer through the public `placeOrder`/`fillOrder` entrypoints, with no reliance on a malicious relayer, prover, or admin — matching the bounty's accepted impact categories (loss/lock of funds, logic attack via missing invariant enforcement).

## Likelihood Explanation
Likelihood is **low-to-medium**: the vulnerable order must be crafted with duplicate `order.output.assets[i].token` entries, which is unusual but not prevented anywhere in `placeOrder` or `_fillSameChain`, unlike the parallel duplicate-*input*-token guard that already exists (`evm/src/apps/IntentGatewayV2.sol:336-337`). Because the input-side guard exists but the output-side one doesn't, this looks like an asymmetric oversight rather than an intentional design choice, making it plausible that it was simply missed rather than deliberately allowed.

## Recommendation
Add the same duplicate-token rejection used for `order.inputs` to `order.output.assets` in `placeOrder`/`_fillSameChain`, or key `_partialFills` (and the fully-filled determination) by `(commitment, outputIndex)` instead of `(commitment, outputToken)` so that legs sharing a token address cannot alias each other's fill-progress accounting.

## Proof of Concept
Not fully verified against a running test harness within the available time — the analysis is based on static code review of `_fillSameChain`, `_withdraw`, and the duplicate-input-token check in `placeOrder`. A background engineer should write a Foundry test analogous to the existing `testPartialFill_RoundingDustReleasedToFinalSolver` tests in `evm/tests/foundry/IntentGatewayV2SameChainTest.sol`, but construct an order with two `output.assets` entries using the **same token address** and amounts chosen so the second leg's `totalRequired` is less than or equal to the first leg's `amount`, then call `fillOrder` fully satisfying the first leg in the same transaction, and confirm either a revert (underflow) or that the second leg's corresponding `order.inputs[j]` escrow remains stuck in the contract while `_filled[commitment]` is already set.

**Uncertainty flagged**: I was not able to fully trace the `_cancelSameChain` / cross-chain cancel paths to confirm with 100% certainty that there is no alternate recovery mechanism for funds locked via this specific duplicate-output-token path, nor did I confirm whether `ExtrinsicIntents.sol` (cross-chain fill path) has an equivalent guard or is unaffected because cross-chain fills are all-or-nothing. A Devin session with test-execution capability should confirm the exact revert/stuck-fund behavior with a concrete Foundry PoC before treating this as fully confirmed.

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L56-57)
```text

        _filled[commitment] = msg.sender;
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L74-79)
```text
            uint256 alreadyFilled = _partialFills[commitment][outputToken];
            uint256 remaining = totalRequired - alreadyFilled;
            if (remaining == 0 || solverAmount == 0) {
                if (solverAmount == 0 && remaining > 0) isFullyFilled = false;
                continue;
            }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L136-138)
```text
        if (isFullyFilled) {
            _execute(order, outputsLen);
            emit OrderFilled({commitment: commitment, filler: msg.sender, outputs: outputFills, inputs: escrowedInputs});
```

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L397-398)
```text
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;
```
