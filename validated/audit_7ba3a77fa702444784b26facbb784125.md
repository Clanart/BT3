Based on the investigation, I found a structurally analogous flaw in the same-chain Intent Gateway partial-fill logic. I could not fully read `placeOrder` in `evm/src/apps/IntentGatewayV2.sol` before running out of iterations (grep confirms it writes to `_orders[commitment][token]` there), so the escrow-accumulation-by-token-address behavior described below is inferred from the withdraw/fill code and the aggregate-by-token-address pattern used everywhere else in the contract, not from directly reading `placeOrder`'s source.

### Title
Shared-token escrow release lets a solver drain unrelated order legs on partial fill - (File: evm/src/apps/intentsv2/IntrinsicIntents.sol)

### Summary
The M-21 bug class is: a per-transaction invariant check (upnl/totalUnrealizedLoss staying equal) is verified locally and independently of the underlying resource (the set of still-open positions) that the invariant is supposed to represent, so repeated, seemingly-valid state transitions can release more value than the resource actually backs. The same shape exists in `IntrinsicIntents._fillSameChain`: the "already filled" invariant is tracked **per output-token leg** (`_partialFills[commitment][outputToken]`), but the actual collateral it releases is read from `_orders[commitment][inputToken]`, an escrow bucket keyed **only by input-token address**, aggregated across every leg of the order that happens to use that token.

### Finding Description
In `_fillSameChain` (`evm/src/apps/intentsv2/IntrinsicIntents.sol:54-149`), for each output asset `i` the code computes: [1](#0-0) 

```
uint256 escrowedAmount;
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
```

When a leg's cumulative fill (`_partialFills[commitment][outputToken]`) reaches `totalRequired`, the code intentionally releases the *entire remaining balance* of `_orders[commitment][order.inputs[i].token]` (this was added to fix rounding-dust locking, per the "Finding #4" comment block in the test suite). This is safe **only if** `order.inputs[i].token` is exclusively backing leg `i`. But `_orders` is keyed purely by token address, aggregated across the whole order (the same mapping is written to at `placeOrder` and drained via `_withdraw` in `evm/src/apps/intentsv2/IntentsBase.sol:390-410`, which likewise only checks `escrowed == 0`, not per-leg attribution).

If an order's `inputs[]` array contains the same token address at two different indices (paired with two different `output.assets` entries), both indices resolve to the same `_orders[commitment][token]` bucket. A solver who fully completes the *smaller* leg first triggers the `amountFilled == totalRequired` branch for that leg and receives `_orders[commitment][token]` in full — including the amount escrowed to back the *other*, still-unfilled leg that shares the same input token. The second leg's later fill then reads an already-zeroed `_orders[commitment][token]`, releases `escrowedAmount = 0`, and `_withdraw`'s `if (amount == 0) continue;` silently skips the transfer instead of reverting — so the loss is masked rather than caught.

This is directly analogous to M-21: a locally-scoped consistency check (`_partialFills[commitment][outputToken]` per leg) is used to authorize a payout drawn from a *shared* aggregate resource (`_orders[commitment][token]`) that spans multiple legs, letting one leg's "completion" event consume value that belongs to another leg.

### Impact Explanation
An order creator (or a solver colluding with the order creator, or a solver acting as their own user) can construct a same-chain order with a cheap/small output leg and an expensive output leg that both use the same input token as collateral. By filling the cheap leg first, the filler drains the input-token escrow meant to cover the expensive leg, receiving far more input tokens than the value of output tokens they provided — a direct loss of protocol/user escrowed funds and unauthorized value extraction, matching the bounty's "stealing or loss of funds" / "transaction manipulation" categories.

### Likelihood Explanation
This requires only an unprivileged user to call `placeOrder` with a specially-crafted `Order` (duplicate input token address across legs) and then call `fillOrder` (or have any solver call it) completing the smaller leg first — no relayer, prover, admin, or governance actor is needed. The main open question (not fully verified due to tool-call exhaustion) is whether `placeOrder` in `evm/src/apps/IntentGatewayV2.sol` explicitly rejects duplicate token addresses across `order.inputs[]`; if it does not, the path is fully attacker-reachable with only standard user-level calls.

### Recommendation
Track escrow per `(commitment, outputLegIndex)` or `(commitment, inputTokenIndex)` rather than aggregating purely by token address, or reject orders at `placeOrder` time whose `inputs[]` contain duplicate token addresses. Alternatively, when releasing the "full remaining balance" on leg completion, cap the release at the leg's own proportional share of `_orders[commitment][token]` rather than the raw bucket balance, and only release genuine rounding dust (a few wei) rather than the whole remaining balance.

### Proof of Concept
1. User places a same-chain order with two output legs, both backed by the same input token `USDC`:
   - Leg A: output = 1 wei of `DAI`, backed by 1000 USDC of the shared `_orders[commitment][USDC]` bucket (nominally "reserved" for leg A is negligible, but the pool is shared).
   - Leg B: output = 1000 DAI, also implicitly drawing from the same `_orders[commitment][USDC]` bucket.
2. `placeOrder` escrows `USDC` into `_orders[commitment][USDC]` (aggregated total for both legs, assuming no duplicate-token check).
3. Solver calls `fillOrder` supplying only the 1 wei DAI required for Leg A. Since `amountFilled == totalRequired` for Leg A, `_fillSameChain` releases `escrowedAmount = _orders[commitment][USDC]` — the **entire** USDC escrow — to the solver for providing 1 wei of DAI.
4. A second solver later fully fills Leg B (1000 DAI) but `_orders[commitment][USDC]` is now 0; `escrowedAmount` computed as 0, `_withdraw` silently `continue`s, and the second solver receives no USDC despite delivering the full DAI amount.
5. Net effect: first solver received the full USDC escrow for a negligible output, protocol/user funds backing Leg B are gone with no revert or event indicating the shortfall.

### Citations

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
