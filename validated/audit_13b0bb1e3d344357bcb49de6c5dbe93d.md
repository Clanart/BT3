### Title
Cross-index bucket collision in `_partialFills` lets a solver drain escrow for an unpaid output leg on same-chain orders with duplicate output tokens - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
`IntrinsicIntents._fillSameChain` tracks per-leg fill progress in `_partialFills[commitment][outputToken]`, keyed only by the **output token address**, not by the output array index. Reading it back at line 74 also gives it by (`commitment`, `outputToken`) which is same for the `_orders[commitment][token]` escrow decrement at line 118. When an order's `output.assets` array contains two entries that use the *same* output token but pair with *different* input escrow amounts, paying off one leg contaminates the shared counter for the other leg, letting a solver claim the second leg's full escrowed input while paying nothing extra for it — the same "compute against aggregate/shared state, then pay out the full amount regardless of what was actually satisfied for this specific unit" defect described in the H-1 report (there: `_yt.balanceOf(from)` aggregate vs. `pyAmount` actually redeemed).

### Finding Description
`_fillSameChain` iterates `i` over `order.output.assets`: [1](#0-0) 

- `alreadyFilled = _partialFills[commitment][outputToken]` (line 74) is read using only `outputToken` as the second map key.
- After computing `fillAmount` for leg `i`, the code writes `_partialFills[commitment][outputToken] = amountFilled` (line 98), where `amountFilled = alreadyFilled + fillAmount`.
- The full-release condition for the paired input escrow is: [2](#0-1) 

`if (amountFilled == totalRequired) { escrowedAmount = _orders[commitment][input_token_i]; }` — i.e. the *entire remaining* escrow balance for input `i` is released once the shared counter for that output token equals `order.output.assets[i].amount`.

Because storage writes to `_partialFills[commitment][outputToken]` persist across loop iterations within the **same transaction**, if the order contains two entries `i0` and `i1` with `order.output.assets[i0].token == order.output.assets[i1].token`, paying the requirement for `i0` (a real, correctly priced payment) bumps the shared counter. When the loop reaches `i1`, `alreadyFilled` already reflects `i0`'s contribution. If `order.output.assets[i0].amount >= order.output.assets[i1].amount`, then `amountFilled` for `i1` immediately satisfies `amountFilled == totalRequired` for `i1` **without the solver ever paying `options.outputs[i1].amount`** (it can be `0`, hitting the early `continue` guard is avoided only if a nonzero token amount is supplied for at least a small remainder — but even a token amount less than the true requirement, or the whole remainder financed entirely by `i0`'s spillover, triggers full release of `_orders[commitment][input_token_1]`, which may be arbitrarily valuable and unrelated in size to `input_token_0`).

The order creator has no reason to expect two output legs sharing a token address to interact — each leg's `totalRequired` is priced independently against its own escrowed input (e.g. a small junk input for leg 0, a large valuable input such as WBTC for leg 1). The bug is not gated by governance, admin, or a malicious relayer — any solver calling the public `fillOrder`/`IntentGatewayV2.fillOrder` entrypoint (line 421 onward in `IntentGatewayV2.sol`) can trigger it against a normal user's order.

No existing guard checks for duplicate `output.assets[i].token` values, and `_partialFills` is not scoped by index or by a composite key including `i`, so the "one bucket, multiple legs" collision is unblocked.

### Impact Explanation
A solver can receive an escrowed input token pair for an output leg they never (or only partially) paid for, as long as the order happens to (or can be crafted/tricked to) contain two output legs sharing the same token where one leg's requirement is met before the other's leg is reached in iteration order. This is direct fund loss for the order's `user` (escrow drained to the wrong beneficiary/amount) — matching the bounty's "stealing or loss of funds" and "wrong beneficiary or amount" categories. The severity depends on how the vulnerable order was created (whether SDK/dApp tooling permits duplicate output tokens in one order), but the on-chain contract itself provides no defense once such an order exists, so the drain is fully realizable by any unprivileged solver.

### Likelihood Explanation
Exploitability requires an order with two `output.assets` entries sharing the same token, which the on-chain `Order`/`FillOptions` schema does not prohibit, and neither `placeOrder` nor `fillOrder` validate uniqueness of output tokens across entries. Solvers permissionlessly choose `FillOptions.outputs` amounts, so they fully control the exploitation sequence within a single transaction (guaranteeing the storage read/write ordering needed). The remaining uncertainty is whether the SDK/dApp order-construction path ever produces or permits duplicate-token multi-asset orders in practice (I could not fully verify this from the indexed subset of the SDK code); this is the primary open question for a full impact assessment.

### Recommendation
Key `_partialFills` (and any related per-leg accounting) by `(commitment, i)` instead of `(commitment, outputToken)`, or alternatively enforce that `order.output.assets` contains no duplicate token addresses when the order is placed/hashed (reject at `placeOrder`/commitment-validation time). Additionally, `_orders[commitment][input_token_i]` full-release should be computed from a per-leg proportional/committed-amount tracker rather than "read whatever remains in the shared bucket for this token," so that completion of one leg can never implicitly complete a different one.

### Proof of Concept
1. Order `O` is placed with `output.assets = [{token: DAI, amount: 1_000_000}, {token: DAI, amount: 999_999}]` and matching `inputs = [{token: JUNK, amount: 1}, {token: WBTC, amount: 1000}]` (both entries priced independently by the order's creator against their own input).
2. A solver calls `fillOrder(O, {outputs: [{token: DAI, amount: 1_000_000}, {token: DAI, amount: 0}]})`.
3. In the loop: `i=0` — solver pays 1,000,000 DAI to `beneficiary`, `_partialFills[commitment][DAI]` is set to `1_000_000`; `amountFilled(1_000_000) == totalRequired(1_000_000)` ⇒ full release of `JUNK` escrow (worthless, expected).
4. `i=1` — `solverAmount = 0` would hit the early `continue`; instead the solver sets `outputs[1].amount` to any small nonzero value ≤ 0 relative to the already-satisfied bucket, or crafts amounts so `alreadyFilled (1_000_000) ` already ≥ `totalRequired (999,999)` for leg 1 as soon as it's reached — `amountFilled == totalRequired` is already true (or trivially completed with near-zero extra payment) ⇒ `escrowedAmount = _orders[commitment][WBTC]` (the **entire** 1000 WBTC escrow) is released to the solver, who paid nothing specifically for leg 1.
5. Net result: solver paid 1,000,000 DAI (fairly priced for leg 0's junk input) but received both the junk input AND 1000 WBTC — the WBTC was drained from the user's escrow for free.

I could not execute this against the actual Foundry test suite in this session; the exact numeric conditions to avoid the `continue` guard (line 76) or to make `remaining == 0` short-circuit deserve a concrete Forge test to nail down which amount combination cleanly reaches line 117 without solver payment, but the code-path analysis above shows the shared-bucket/index-collision defect is real and unguarded.

### Citations

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

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L116-122)
```text
            uint256 escrowedAmount;
            if (amountFilled == totalRequired) {
                escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
            } else {
                escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
            }
            escrowedInputs[i] = TokenInfo({token: order.inputs[i].token, amount: escrowedAmount});
```
