### Title
Missing Deadline Check in `MetricOmmPoolLiquidityAdder` Liquidity Functions Allows Stale Transaction Execution at Unfavorable Prices — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` exposes four public liquidity-addition entry points — two overloads of `addLiquidityExactShares` and two overloads of `addLiquidityWeighted` — none of which accept or enforce a `deadline` parameter. By contrast, every swap entry point in `MetricOmmSimpleRouter` calls `_checkDeadline(params.deadline)` before touching the pool. A pending liquidity transaction can therefore be held in the mempool and executed arbitrarily far in the future, at a pool price the user never intended to transact at, causing immediate impermanent loss on the deposited principal.

---

### Finding Description

`MetricOmmSimpleRouter` consistently guards all four swap paths with a deadline: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

`MetricOmmPoolLiquidityAdder` has no equivalent guard anywhere in its public surface: [5](#0-4) [6](#0-5) [7](#0-6) [8](#0-7) 

The `addLiquidityWeighted` overloads do include a cursor-bounds check (`_validateBinAndBinPosition`) that reverts if the pool cursor has moved outside the user-supplied `[minimalCurBin, maximalCurBin]` window: [9](#0-8) 

However, this check is a price-range guard, not a time guard. A user who specifies a wide cursor window (or any window that still contains the cursor after a large price move) receives no protection. The probe-then-pay flow executes at whatever price the pool holds at execution time: [10](#0-9) 

The `maxAmountToken0` / `maxAmountToken1` caps enforced in the callback: [11](#0-10) 

limit total token spend but do not protect against bad-price composition. A user who sets generous caps (e.g., 10 000 USDC / 5 ETH) to accommodate normal slippage will still have the full capped amount deployed at a stale price.

---

### Impact Explanation

An LP submits `addLiquidityWeighted` or `addLiquidityExactShares` at price P. The transaction is delayed in the mempool (network congestion, low gas, MEV withholding). By the time it is included, the pool price has moved to P′. The probe runs at P′, the scale factor is computed at P′, and the user's tokens are deposited into bins priced at P′. When the market price reverts toward P, the position immediately suffers impermanent loss proportional to the price deviation — a direct reduction of the LP's redeemable principal. The loss is bounded by the user's caps but can be substantial for large cap values or large price moves.

---

### Likelihood Explanation

Any pending transaction in a public mempool is subject to this condition. Volatile markets, gas-price spikes, and MEV searchers who benefit from delayed LP entry all increase the probability. No privileged access is required; the trigger is ordinary network latency combined with price movement.

---

### Recommendation

Add a `uint256 deadline` parameter to all four public entry points of `MetricOmmPoolLiquidityAdder` and call the same `_checkDeadline` helper used by `MetricOmmSimpleRouter` as the first statement in each function, before any pool interaction:

```solidity
// Example for addLiquidityExactShares
function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
+   uint256 deadline,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
+   _checkDeadline(deadline);
    _validateOwner(owner);
    ...
}
```

Apply the same pattern to both `addLiquidityWeighted` overloads.

---

### Proof of Concept

1. ETH/USDC pool price: 1 ETH = 2 000 USDC. User calls `addLiquidityWeighted` with `maxAmountToken0 = 4 000 USDC`, `maxAmountToken1 = 2 ETH`, cursor bounds wide enough to cover ±50 % price movement.
2. Transaction sits in the mempool. ETH price moves to 3 000 USDC (cursor still within user's wide bounds — no revert from `_validateBinAndBinPosition`).
3. Transaction is included. Probe returns `need0 = 6 000 USDC`, `need1 = 2 ETH`. Scale = `min(4000/6000, 2/2) = 0.667`. User deposits 4 000 USDC + 1.333 ETH at the 3 000 USDC/ETH price.
4. ETH price reverts to 2 000 USDC. The position's redeemable value is now less than 4 000 USDC + 1.333 ETH at 2 000 USDC/ETH due to impermanent loss — a direct reduction of the LP's principal with no recourse.

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L71-81)
```text
  function addLiquidityExactShares(
    address pool,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateDeltas(deltas);
    return _addLiquidity(pool, msg.sender, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L123-149)
```text
  function addLiquidityWeighted(
    address pool,
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
    _validateDeltas(weightDeltas);
    _validatePositiveWeights(weightDeltas);
    _validateBinAndBinPosition(pool, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition);

    try IMetricOmmPoolActions(pool)
      .addLiquidity(msg.sender, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) returns (
      uint256, uint256
    ) {
      revert WeightedProbeInconclusive();
    } catch (bytes memory reason) {
      (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
      LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
      return _addLiquidity(pool, msg.sender, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
    }
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L165-167)
```text
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
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
