## Title
Shared `_partialFills` key across output indices with the same output token lets a solver drain multiple escrow legs for less than the required total — (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

## Summary
`IntrinsicIntents._fillSameChain` tracks same-chain partial-fill progress in `_partialFills[commitment][outputToken]`, keyed only by the *output token address*, while the fill-completion check (`amountFilled == totalRequired`) is evaluated per array index against that index's own `totalRequired`. `IntentGatewayV2.placeOrder` deduplicates *input* tokens (`_orders[commitment][token] != 0` reverts) but never deduplicates *output* tokens. When an order has two or more output legs that use the same output token but different (and differently valuable) escrowed inputs, payments toward one leg's requirement silently count toward another leg's "already filled" total because they share the same map key. This lets a solver complete a cheap leg first, then complete an expensive leg by paying only the *marginal* difference between the two `totalRequired` values instead of that leg's full required amount — extracting the cheap leg's large escrow essentially for free.

This is structurally the same class of bug as the Perennial-V2 `closable` report: a derived progress/quota value (`closable` there, cumulative-fill-progress here) is computed by a different, non-authoritative accounting path (shared key vs. per-leg truth) than the value the protocol actually intends to enforce, letting settlement proceed on an incorrect amount.

## Finding Description
In `placeOrder`, duplicate **input** tokens are explicitly rejected: [1](#0-0) 

but there is no equivalent uniqueness check on `order.output.assets[i].token`. `_fillSameChain` then tracks per-leg progress keyed only by that token: [2](#0-1) 

and releases the escrowed *input* for that index in full once that index's own `totalRequired` is reached: [3](#0-2) 

Because `alreadyFilled = _partialFills[commitment][outputToken]` is a single running total shared across every index that uses the same output token, an index's "already filled" figure can include payments that were nominally directed at a *different* order leg. Concretely, for an order with:
- index 0: `output = {token: USDC, amount: 999}`, `input = {token: A, amount: 500 A}`
- index 1: `output = {token: USDC, amount: 1000}`, `input = {token: B, amount: 5000 B}`

a solver first fully fills index 0 by paying 999 USDC → `_partialFills[commitment][USDC] = 999`, releasing all 500 A. In a second call, index 1 is evaluated with `alreadyFilled = 999` already carried over from index 0's payment, so `remaining = 1000 - 999 = 1`; paying just 1 more USDC makes `amountFilled == totalRequired (1000)` for index 1, releasing the full 5000 B.

Total paid by the solver: 1000 USDC. Total value released to the solver: 500 A + 5000 B, which the order creator intended to cost 999 + 1000 = 1999 USDC combined. The solver captures 999 USDC worth of value (token A's entire escrow) essentially for free, at the order creator's expense.

## Impact Explanation
This is a direct loss-of-funds bug for the order creator: escrowed tokens (up to an entire leg's worth) are released to an unprivileged solver without the solver paying the amount the order creator required for that leg. No malicious relayer, prover, governance actor, or leaked key is needed — any solver calling the public `fillOrder` entrypoint in the intended sequence triggers it. This falls squarely within the bounty's "stealing or loss of funds" / "logic attack" impact categories.

## Likelihood Explanation
The precondition (an order with two or more output legs sharing the same output token but differently-valued paired inputs) is fully attacker/user-constructible since `placeOrder` never rejects duplicate output tokens (only duplicate input tokens are rejected). Any solver monitoring `OrderPlaced` events can detect such an order and exploit it with two ordinary `fillOrder` calls, no special privileges or timing constraints required.

## Recommendation
Key `_partialFills` by `(commitment, outputIndex)` instead of `(commitment, outputToken)`, or alternatively reject orders whose `output.assets` contain duplicate tokens at `placeOrder` time (mirroring the existing input-token duplicate check). Either fix restores a 1:1 mapping between an output leg's tracked progress and its own `totalRequired`, preventing cross-leg credit leakage.

## Proof of Concept
1. User calls `placeOrder` with an order where:
   - `output.assets[0] = {token: USDC, amount: 999}`, `inputs[0] = {token: A, amount: 500e18}`
   - `output.assets[1] = {token: USDC, amount: 1000}`, `inputs[1] = {token: B, amount: 5000e18}`
   (input tokens A and B are distinct, so the input-uniqueness check in `placeOrder` passes; output token USDC repeats, which is unchecked.)
2. Solver calls `fillOrder` providing `options.outputs[0].amount = 999`, `options.outputs[1].amount = 0` → index 0 completes (`_partialFills[commitment][USDC] = 999`), releasing all 500 A to the solver per [3](#0-2) .
3. Solver calls `fillOrder` again with `options.outputs[0].amount = 0`, `options.outputs[1].amount = 1` → at index 1, `alreadyFilled = _partialFills[commitment][USDC] = 999` (carried over from index 0), `remaining = 1`, so `fillAmount = 1` and `amountFilled = 1000 == totalRequired`, fully releasing all 5000 B per the same code path.
4. Total solver payment: 1000 USDC. Total value extracted: 500 A + 5000 B (intended cost 1999 USDC), demonstrating the fund-loss primitive.

### Citations

**File:** evm/src/apps/IntentGatewayV2.sol (L334-343)
```text
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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L66-98)
```text
        for (uint256 i; i < outputsLen; i++) {
            bytes32 outputToken = order.output.assets[i].token;
            if (options.outputs[i].token != outputToken) revert InvalidInput();

            address token = address(uint160(uint256(outputToken)));
            uint256 totalRequired = order.output.assets[i].amount;
            uint256 solverAmount = options.outputs[i].amount;

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
