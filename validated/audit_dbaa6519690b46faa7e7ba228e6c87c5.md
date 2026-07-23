### Title
Missing Deadline Parameter in All `addLiquidity*` Functions Allows Stale Liquidity Deposits at Adverse Oracle Prices — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

### Summary

`MetricOmmPoolLiquidityAdder` exposes four public liquidity-addition entry points — two overloads of `addLiquidityExactShares` and two overloads of `addLiquidityWeighted` — none of which accept or enforce a `deadline` parameter. The sibling `MetricOmmSimpleRouter` contract explicitly calls `_checkDeadline(params.deadline)` at the top of every swap entry point, demonstrating that the protocol recognises the need for deadline protection on time-sensitive operations. The omission in the liquidity adder means a pending transaction can be mined arbitrarily late, after the oracle price has moved, causing the user to deposit tokens into bins priced at a materially different rate than intended.

### Finding Description

`MetricOmmSwapRouterBase._checkDeadline` is defined and used consistently across all four router swap functions: [1](#0-0) 

Every swap entry point in `MetricOmmSimpleRouter` calls it as the very first statement: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

None of the four `MetricOmmPoolLiquidityAdder` entry points have an equivalent guard. The signatures in the interface confirm no `deadline` field exists anywhere in the parameter set: [6](#0-5) [7](#0-6) 

The `addLiquidityWeighted` path does check cursor bounds at execution time via `_validateBinAndBinPosition`, which reads the live `slot0` cursor: [8](#0-7) 

However, this check only rejects execution when the oracle cursor has moved **outside** the user-supplied `[minimalCurBin, maximalCurBin]` window. Any price movement that stays within that window — which users must set wide enough to tolerate normal volatility — allows the transaction to execute at the new, unintended oracle price. `addLiquidityExactShares` has no cursor check at all. [9](#0-8) 

### Impact Explanation

Metric OMM is a pure-oracle AMM: the pool's bid/ask quotes are derived entirely from the external price provider at swap time. Liquidity deposited into a bin is immediately exposed to the live oracle price. If a user's `addLiquidity` transaction is mined after the oracle price has moved significantly (but within the user's cursor window), the user's tokens are locked into bins priced at the new rate. Any subsequent swap against those bins executes at the new oracle price, causing the LP to absorb the full price-move as impermanent loss from the moment of deposit — a direct loss of user principal with no recovery path short of removing liquidity at a loss.

For `addLiquidityExactShares`, the exposure is even broader: there is no cursor-bounds check, so the transaction executes regardless of how far the oracle has moved.

### Likelihood Explanation

The scenario requires a transaction to remain pending in the mempool long enough for the oracle price to move materially. This is realistic on Ethereum mainnet (one of the three target chains) during periods of network congestion, and has been observed in practice (e.g., during the Arbitrum airdrop referenced by the original judge). The user has no on-chain mechanism to cancel or time-bound the operation once submitted. The `maxAmountToken0`/`maxAmountToken1` caps protect only against the pool pulling *more tokens than approved*, not against the user depositing at an adverse price within those caps.

### Recommendation

Add a `uint256 deadline` parameter to all four `addLiquidity*` entry points in both `IMetricOmmPoolLiquidityAdder` and `MetricOmmPoolLiquidityAdder`, and call `_checkDeadline(deadline)` (or an equivalent inline check) as the first statement in each function, mirroring the pattern already used in `MetricOmmSimpleRouter`. Since `MetricOmmPoolLiquidityAdder` does not inherit `MetricOmmSwapRouterBase`, the check can be inlined:

```solidity
if (block.timestamp > deadline) revert TransactionExpired(deadline, block.timestamp);
```

### Proof of Concept

1. Alice calls `addLiquidityWeighted` on an ETH/USDC pool when the oracle price is $2 000/ETH. She sets `minimalCurBin = -5`, `maximalCurBin = 5` (a ±5-bin window around the current cursor) and `maxAmountToken0 = 1 ETH`, `maxAmountToken1 = 2 000 USDC`. She submits with a low gas price.
2. Network congestion keeps the transaction pending. The oracle price drops to $1 800/ETH — still within Alice's ±5-bin window, so `_validateBinAndBinPosition` does not revert.
3. The transaction is mined. The probe runs at the $1 800 cursor; shares are scaled to fit Alice's caps. Alice's tokens are deposited into bins priced at $1 800.
4. A swap immediately executes against Alice's position at the $1 800 oracle price. Alice has effectively sold ETH at $1 800 instead of $2 000 — a $200/ETH loss — with no recourse.
5. Had a `deadline` been present and set to, e.g., `block.timestamp + 5 minutes` at signing time, the transaction would have reverted in step 3 and Alice's funds would have been safe. [10](#0-9) [9](#0-8)

### Citations

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L91-94)
```text
  function _checkDeadline(uint256 deadline) internal view {
    // forge-lint: disable-next-line(block-timestamp)
    if (block.timestamp > deadline) revert IMetricOmmSimpleRouter.TransactionExpired(deadline, block.timestamp);
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-68)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-93)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-131)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-155)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L87-95)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable returns (uint256 amount0Added, uint256 amount1Added);
```

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L132-144)
```text
  function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable returns (uint256 amount0Added, uint256 amount1Added);
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
```text
  function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L88-116)
```text
  function addLiquidityWeighted(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata weightDeltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L263-286)
```text
  function _validateBinAndBinPosition(
    address pool,
    int8 minimalCurBin,
    uint104 minimalPosition,
    int8 maximalCurBin,
    uint104 maximalPosition
  ) internal view {
    if (minimalCurBin > maximalCurBin) {
      revert CursorOutOfBounds(0, 0, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }

    (, int8 curBinIdx, uint104 curPosInBin,,,) = PoolStateLibrary._slot0(pool);

    int256 curBin = curBinIdx;
    if (curBin < minimalCurBin || curBin > maximalCurBin) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
    if (curBinIdx == minimalCurBin && curPosInBin < minimalPosition) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
    if (curBinIdx == maximalCurBin && curPosInBin > maximalPosition) {
      revert CursorOutOfBounds(curBinIdx, curPosInBin, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);
    }
  }
```
