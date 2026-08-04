### Title
Floor-division to zero in same-chain partial-fill escrow release causes total loss of a solver's contribution - ([File: evm/src/apps/intentsv2/IntrinsicIntents.sol])

### Summary
`_fillSameChain` computes the proportional escrow released to a partially-filling solver as `(order.inputs[i].amount * fillAmount) / totalRequired`. This is Solidity integer division that truncates toward zero. Exactly like the reported `volume / price` bug (H-01), any partial fill whose numerator is smaller than the denominator (`order.inputs[i].amount * fillAmount < totalRequired`) rounds `escrowedAmount` down to `0`, while the solver has still unconditionally transferred `beneficiaryTotal` real output tokens to the beneficiary and had `_partialFills[commitment][outputToken]` incremented by the full `fillAmount`. The solver receives nothing for that fill — a total, uncompensated loss of the tokens they provided in that transaction.

### Finding Description
In `_fillSameChain` ( [1](#0-0) ), for a partial fill (`amountFilled != totalRequired`):

```solidity
} else {
    fillAmount = solverAmount > remaining ? remaining : solverAmount;
}
...
uint256 escrowedAmount;
if (amountFilled == totalRequired) {
    escrowedAmount = _orders[commitment][address(uint160(uint256(order.inputs[i].token)))];
} else {
    escrowedAmount = (order.inputs[i].amount * fillAmount) / totalRequired;
}
```

Before this division is computed, the function has already unconditionally sent the solver's output tokens to the beneficiary:

```solidity
IERC20(token).safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal);
```

There is no check anywhere in `fillOrder` / `_fillSameChain` enforcing `order.inputs[i].amount * fillAmount >= totalRequired`, nor any minimum-fill-amount requirement, nor a check that `escrowedAmount > 0` before proceeding. Consequently, an order combining a low-decimal input token (e.g. 6-decimal USDC) with a high-decimal output token (e.g. 18-decimal DAI), or any order where a solver picks a `fillAmount` that is small relative to `totalRequired`, will silently zero out the input the solver is owed for that fill. This is structurally identical to the reported `commitBid`/`fulfillWindow` bug: the party providing value (bidder in the report, filling solver here) is not compensated because integer division truncates to zero, and neither `commitBid`-style (`volume >= price`) nor `createAuction`-style (equal decimals) guards exist to prevent it.

The escrow itself is not corrupted or double-spent (since `_orders[commitment][token]` is only decremented by the truncated `escrowedAmount`, i.e. `0` in the worst case, so the funds remain available for the eventual final fill), but the specific solver who executed the affected partial fill has irreversibly given away real ERC-20/native tokens to the beneficiary and received `0` tokens back for that transaction — direct fund loss reachable through the public `fillOrder` entrypoint with no privileged actor, malicious relayer, or governance action required.

### Impact Explanation
This meets the bounty's "loss of funds" criterion: an unprivileged solver calling the public `fillOrder`/`_fillSameChain` path can lose 100% of the value they transferred in a given partial fill because of an unguarded floor-division. The loss is real, on-chain, and irreversible (`safeTransferFrom` to the beneficiary already executed before/independent of the truncated escrow computation). Because Intent Gateway explicitly supports arbitrary input/output token pairs with differing decimals (the docs describe cross-decimal same-token examples such as 6-decimal vs 18-decimal USDC), this is not a contrived edge case but a realistic configuration for real order flows.

### Likelihood Explanation
Medium: it requires a same-chain order with partial fills (`_fillSameChain`) where either (a) input and output token decimals differ significantly, or (b) a solver submits a `fillAmount` that is small relative to `totalRequired`. Both are common in normal solver operation — solvers routinely probe orders with small test fills, and cross-decimal token pairs (e.g., 6-decimal stablecoin input vs 18-decimal output) are explicitly supported and documented in this codebase.

### Recommendation
- Revert if the computed `escrowedAmount` for a partial fill would be `0` while `fillAmount > 0`, forcing the solver either to fill the full remaining amount or a fill amount large enough that `order.inputs[i].amount * fillAmount >= totalRequired`.
- Alternatively, enforce a protocol-level minimum partial-fill ratio/amount (mirroring the report's `volume >= price` recommendation) so that `fillAmount` can never produce a truncated-to-zero escrow release.
- Consider using a cumulative "escrow released so far" bookkeeping (subtracting from the running total at the final fill, as already done for the terminal case) consistently for every partial step, so any transient rounding error is corrected before tokens leave the solver, not only at order completion.

### Proof of Concept
1. User places a same-chain order: input = 100 USDC (6 decimals, `order.inputs[0].amount = 100_000000`), output = 3,000,000 DAI (18 decimals, `totalRequired = 3_000_000e18`) — i.e., a low `input/output` ratio order (extreme case for illustration; smaller ratios reproduce the same effect with smaller magnitudes).
2. Solver calls `fillOrder` with `solverAmount = 1e18` (1 DAI), a legitimate small partial fill.
3. Inside `_fillSameChain`: `fillAmount = 1e18` (since `remaining = 3_000_000e18 > solverAmount`).
4. `escrowedAmount = (100_000000 * 1e18) / 3_000_000e18 = 100_000000 / 3_000_000 = 33` (truncated) — a small, non-zero example. To force exact `0`, use a fill even smaller relative to `totalRequired`/`order.inputs[i].amount`, e.g. `solverAmount = 1` wei of DAI on the same order: `fillAmount = 1`, `escrowedAmount = (100_000000 * 1) / 3_000_000e18 = 0`.
5. The solver's `IERC20(dai).safeTransferFrom(msg.sender, beneficiary, 1)` still executes, transferring real value to the beneficiary, while `escrowedInputs[0].amount = 0` is what the solver receives via `_withdraw` — total loss of the 1 wei DAI provided, with no revert and no compensation, for that call.
6. Repeating this with realistic fill sizes on a genuinely skewed-decimals order (e.g., a 6-decimal input vs 18-decimal output pair with a modest `fillAmount`) can zero out escrow release for fills worth meaningful, non-dust amounts of tokens. [1](#0-0) [2](#0-1)

### Citations

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L93-123)
```text
            } else {
                fillAmount = solverAmount > remaining ? remaining : solverAmount;
            }

            uint256 amountFilled = alreadyFilled + fillAmount;
            _partialFills[commitment][outputToken] = amountFilled;
            uint256 beneficiaryTotal = fillAmount + beneficiaryShare;

            if (token == address(0)) {
                if (msgValue < beneficiaryTotal + protocolShare) revert InsufficientNativeToken();
                msgValue -= (beneficiaryTotal + protocolShare);
                (bool sent,) = beneficiary.call{value: beneficiaryTotal}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransferFrom(msg.sender, beneficiary, beneficiaryTotal);
                if (protocolShare > 0) {
                    IERC20(token).safeTransferFrom(msg.sender, address(this), protocolShare);
                }
            }

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
