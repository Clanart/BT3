Audit Report

## Title
Missing Deadline Parameter in All `addLiquidity*` Entry Points Allows Stale Liquidity Deposits at Adverse Oracle Prices — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary

All four public entry points in `MetricOmmPoolLiquidityAdder` — two overloads of `addLiquidityExactShares` and two overloads of `addLiquidityWeighted` — accept no `deadline` parameter and perform no timestamp check. A pending transaction can be mined arbitrarily late after the oracle price has moved, causing the LP to deposit tokens at a materially different price than intended, with no on-chain recourse. The sibling `MetricOmmSimpleRouter` calls `_checkDeadline(params.deadline)` as the first statement in every swap entry point, confirming the protocol recognises this requirement for time-sensitive operations.

## Finding Description

`MetricOmmSwapRouterBase._checkDeadline` is defined and called at the top of all four swap entry points in `MetricOmmSimpleRouter`:

- `exactInputSingle` (L68), `exactInput` (L93), `exactOutputSingle` (L131), `exactOutput` (L155)

None of the four `MetricOmmPoolLiquidityAdder` entry points contain an equivalent guard:

- `addLiquidityExactShares(pool, owner, salt, deltas, max0, max1, ext)` — L56–68: calls `_validateOwner`, `_validateDeltas`, then immediately delegates to `_addLiquidity`. No timestamp check.
- `addLiquidityExactShares(pool, salt, deltas, max0, max1, ext)` — L71–81: same, no timestamp check.
- `addLiquidityWeighted(pool, owner, ...)` — L88–116: calls `_validateBinAndBinPosition` which reads the live `slot0` cursor and reverts only if the cursor has moved **outside** `[minimalCurBin, maximalCurBin]`. Any oracle price movement that stays within that window — which users must set wide enough to tolerate normal volatility — allows execution at the new, unintended price.
- `addLiquidityWeighted(pool, salt, ...)` — L123–149: same partial mitigation.

The `addLiquidityExactShares` overloads have no cursor check at all; they execute regardless of how far the oracle has moved since the transaction was signed.

Root cause: `MetricOmmPoolLiquidityAdder` does not inherit `MetricOmmSwapRouterBase` and no inline `block.timestamp > deadline` guard was added as a substitute.

## Impact Explanation

Metric OMM is a pure-oracle AMM: bin bid/ask quotes are derived entirely from the external price provider at execution time. Liquidity deposited into a bin is immediately priced at the live oracle rate. If a user's `addLiquidity` transaction is mined after the oracle price has moved materially (but within the cursor window for weighted, or unconditionally for exact-shares), the user's tokens are locked into bins priced at the new rate. Any subsequent swap against those bins executes at the new oracle price, causing the LP to absorb the full price-move as impermanent loss from the moment of deposit — a direct loss of user principal with no recovery path short of removing liquidity at a loss. This satisfies the "direct loss of user principal" criterion above Sherlock Medium thresholds.

## Likelihood Explanation

The scenario requires a transaction to remain pending in the mempool long enough for the oracle price to move materially. This is realistic on Ethereum mainnet (one of the target chains) during periods of network congestion. The user has no on-chain mechanism to cancel or time-bound the operation once submitted. The `maxAmountToken0`/`maxAmountToken1` caps protect only against the pool pulling more tokens than approved, not against depositing at an adverse price within those caps. The `[minimalCurBin, maximalCurBin]` window for `addLiquidityWeighted` must be set wide enough to tolerate normal volatility, leaving a meaningful price-move range in which the transaction silently executes at the wrong price.

## Recommendation

Add a `uint256 deadline` parameter to all four `addLiquidity*` entry points in both `IMetricOmmPoolLiquidityAdder` and `MetricOmmPoolLiquidityAdder`, and add an inline check as the first statement in each function, since `MetricOmmPoolLiquidityAdder` does not inherit `MetricOmmSwapRouterBase`:

```solidity
if (block.timestamp > deadline) revert TransactionExpired(deadline, block.timestamp);
```

This mirrors the pattern already used in `MetricOmmSimpleRouter` via `_checkDeadline`.

## Proof of Concept

1. Alice calls `addLiquidityWeighted` on an ETH/USDC pool when the oracle price is $2,000/ETH. She sets `minimalCurBin = -5`, `maximalCurBin = 5` and `maxAmountToken0 = 1 ETH`, `maxAmountToken1 = 2,000 USDC`. She submits with a low gas price.
2. Network congestion keeps the transaction pending. The oracle price drops to $1,800/ETH — still within Alice's ±5-bin window, so `_validateBinAndBinPosition` (L263–286) does not revert.
3. The transaction is mined. The probe runs at the $1,800 cursor; shares are scaled to fit Alice's caps. Alice's tokens are deposited into bins priced at $1,800.
4. A swap immediately executes against Alice's position at the $1,800 oracle price. Alice has effectively sold ETH at $1,800 instead of $2,000 — a $200/ETH loss — with no recourse.
5. For `addLiquidityExactShares`, the exposure is unbounded: there is no cursor check at all (L56–81), so the transaction executes regardless of how far the oracle has moved.
6. A Foundry fork test can reproduce this by: (a) submitting `addLiquidityWeighted` at block N, (b) advancing time and pushing the oracle to a new price within the cursor window, (c) mining the transaction at block N+k and asserting the deposited bin price differs from the intended price.