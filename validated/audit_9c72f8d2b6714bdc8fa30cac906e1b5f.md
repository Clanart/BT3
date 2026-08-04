## Title
Same-chain partial-fill escrow release drains cross-leg collateral for orders sharing an input token - (File: `evm/src/apps/intentsv2/IntrinsicIntents.sol`)

### Summary
`IntrinsicIntents._fillSameChain` computes the escrow amount to release to a solver differently depending on whether the *current leg* just reached its target: if `amountFilled == totalRequired` it releases `_orders[commitment][token]` — the entire remaining escrow balance stored under that token address for the whole order — instead of the amount owed for that specific leg. Because `_orders` is keyed only by `(commitment, token)` and not by leg index, an order whose `inputs[]` array uses the same token address for more than one leg allows a solver to fully-fill just one leg and receive the collateral belonging to the other, still-unfilled leg(s) as well.

### Finding Description
In `_fillSameChain`: [1](#0-0) 

```solidity
uint256 escrowedAmount;
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
```

The `amountFilled == totalRequired` branch was added (per the accompanying test, `testPartialFill_RoundingDustReleasedToFinalSolver`) to hand the last partial-fill solver whatever integer-division dust is left in escrow, rather than a truncated proportional share — a legitimate fix in the single-leg case.

The problem is that `_orders[commitment][token]` is a **per-token**, not **per-leg**, balance: [2](#0-1) 

```solidity
function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
    ...
    for (uint256 i; i < len; i++) {
        address token = address(uint160(uint256(body.tokens[i].token)));
        uint256 amount = body.tokens[i].amount;
        if (amount == 0) continue;

        uint256 escrowed = _orders[body.commitment][token];
        if (escrowed == 0) revert UnknownOrder();

        _orders[body.commitment][token] = escrowed - amount;
        ...
```

`order.inputs[i]` and `order.output.assets[i]` are parallel, index-aligned legs (the same "leg" model used elsewhere in the codebase, e.g. the multi-leg fill logic in `sdk/packages/simplex/src/strategies/fx.ts`). Nothing in `_fillSameChain` or in order placement prevents two different legs `i` and `j` from sharing the same `inputs[i].token == inputs[j].token` while backing two independent output legs (e.g. a user escrows 1000 USDC split as `inputs[0].token = USDC` for an output-DAI leg and `inputs[2].token = USDC` for an output-USDT leg). Escrow for both legs accumulates into the same `_orders[commitment][USDC]` slot at placement time.

When a solver fully fills leg 0 (reaching `amountFilled == totalRequired` for that leg), the code does not compute "the USDC still reserved for leg 0"; it reads and hands out the *entire* current balance of `_orders[commitment][USDC]`, which also still contains the USDC reserved to pay whichever solver eventually fills leg 2. `_withdraw` then zeroes/decrements that same shared slot. The second leg's fill later calls `_withdraw` with `escrowed == 0` (or an incorrectly reduced value) and reverts with `UnknownOrder()`, permanently stranding the user's remaining escrow while the first solver has already been paid the collateral of both legs for delivering only one leg's output.

This is structurally the same bug pattern as the reported `max_flashloan` issue: a "how much can I claim right now" computation (`max_flashloan` = `cap - totalSupply`; here `escrowedAmount` = full `_orders[commitment][token]`) is derived from an aggregate/global value without checking a legitimate reservation/cap held elsewhere (PSP22Capped's cap; here, the other leg's untouched share of the same token-keyed escrow bucket) — leading to an incorrect amount being paid out.

### Impact Explanation
This is reachable by any unprivileged solver simply calling `fillOrder` on the destination chain (same-chain settlement, no relayer/prover/admin involved) and matches the bounty's required impact classes: **unauthorized transaction/execution** (the solver receives tokens it never earned) and **loss of user funds** (the user's escrow reserved for a still-open leg is transferred to a party who did not fill that leg, and the leg becomes permanently unfillable/`UnknownOrder`). No malicious relayer, prover, governance actor, or front-running is required — a single solver constructing/observing an order with a repeated input token and choosing which leg to fill first is sufficient.

### Likelihood Explanation
Likelihood depends on whether users/integrators actually construct same-chain multi-leg orders that reuse the same input token address across two or more `inputs[]`/`output.assets[]` legs. The `Order`/leg data structures and the parallel `inputs[i]`/`output.assets[i]` indexing used throughout `IntrinsicIntents.sol`/`ExtrinsicIntents.sol` place no restriction preventing this, and the SDK's multi-leg fx strategy (`sdk/packages/simplex/src/strategies/fx.ts`) already treats each `(input, output)` pair as an independent leg, suggesting duplicate-input-token multi-leg orders are a plausible real usage pattern rather than a purely theoretical corner case.

### Recommendation
Track per-leg reserved/escrowed amounts (e.g., key `_orders` by `(commitment, i)` or maintain a separate `_partialFills`-style remaining-per-leg accounting) instead of relying on the aggregate `_orders[commitment][token]` balance when deciding how much to release on a leg's completion. The "release remaining dust" fix for `amountFilled == totalRequired` should only ever release the dust attributable to that specific leg's own reserved share, never the shared token-keyed bucket that may still be backing other unfilled legs.

### Proof of Concept
1. User places a same-chain order with two legs that both use USDC as input:
   - Leg 0: `inputs[0] = {token: USDC, amount: 500}`, `output.assets[0] = {token: DAI, amount: 450}`
   - Leg 1: `inputs[1] = {token: USDC, amount: 500}`, `output.assets[1] = {token: USDT, amount: 450}`
   - At placement, `_orders[commitment][USDC] = 1000` (both legs' escrow sums into one slot).
2. Solver A calls `fillOrder` providing only `outputs[0] = {token: DAI, amount: 450}` and `outputs[1] = {token: USDT, amount: 0}` (or simply fills leg 0 to completion in one call while leg 1 remains untouched/zero).
3. For leg 0, `amountFilled == totalRequired` (450 == 450) is true, so `escrowedAmount = _orders[commitment][USDC]` = **1000**, not the 500 actually owed for leg 0.
4. `_withdraw` decrements `_orders[commitment][USDC]` from 1000 to 0 and transfers **1000 USDC** to Solver A, who only delivered the DAI leg.
5. When a second solver later tries to fill leg 1 (USDT leg), `_withdraw` finds `_orders[commitment][USDC] == 0` and reverts with `UnknownOrder()` — leg 1 can never be filled, and the user has permanently lost the 500 USDC that was meant to back it.

**Uncertainty:** I was not able to view the full `placeOrder`/escrow-accumulation code in `IntentsBase.sol` (only `_withdraw` and `_cancelSameChain` were retrieved) to confirm the exact statement used to populate `_orders[commitment][token]` at order placement, so I cannot cite the precise line that sums duplicate-token legs into one slot. Given `_withdraw`'s and `_fillSameChain`'s use of `_orders[commitment][token]` as a single per-token balance, this summation is the only consistent reading, but confirming it (and checking whether `placeOrder` validates against duplicate tokens across legs) would benefit from a full read of `IntentsBase.sol`'s placement logic in a Devin session with complete file access.

### Citations

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

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-409)
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
```
