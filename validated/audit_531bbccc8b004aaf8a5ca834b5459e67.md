### Title
No deadline protection on `MetricOmmPoolLiquidityAdder` liquidity functions allows stale-price deposits - (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

### Summary

`MetricOmmPoolLiquidityAdder` exposes four public liquidity-add entry points (`addLiquidityExactShares` × 2, `addLiquidityWeighted` × 2) with no `deadline` parameter and no `_checkDeadline` call. The sibling `MetricOmmSimpleRouter` enforces a deadline on every swap entry point. A pending `addLiquidity` transaction that sits in the mempool will execute against whatever oracle price is live at inclusion time, depositing the LP's tokens at a price they never approved.

### Finding Description

`MetricOmmSimpleRouter` calls `_checkDeadline(params.deadline)` at the top of every swap function. [1](#0-0) [2](#0-1) 

`MetricOmmPoolLiquidityAdder` has no such check anywhere in its public surface. [3](#0-2) [4](#0-3) 

The `_checkDeadline` helper exists only in `MetricOmmSwapRouterBase` and is never imported or called by `MetricOmmPoolLiquidityAdder`. [5](#0-4) 

For `addLiquidityExactShares`, the token amounts the pool requests in the callback are computed from the live oracle price at execution time. The only guard is `maxAmountToken0`/`maxAmountToken1`, which caps total spend but does not constrain the price ratio at which tokens are deposited. [6](#0-5) 

For `addLiquidityWeighted`, the probe-and-scale flow reads the pool cursor at execution time. The cursor-bounds check (`minimalCurBin`/`maximalCurBin`) only verifies the bin index, not the oracle price within that bin. The oracle price can move substantially inside a single bin without triggering `CursorOutOfBounds`. [7](#0-6) 

### Impact Explanation

When a transaction is delayed in the mempool (network congestion, gas price underbid, MEV reordering), the oracle price can move. The LP's tokens are then deposited at the new price ratio. The LP immediately holds a position with impermanent loss relative to their intended entry price, constituting a direct loss of deposited principal. The `maxAmountToken0`/`maxAmountToken1` caps do not prevent this: they only bound total spend, not the price at which the deposit occurs.

### Likelihood Explanation

Any period of network congestion or gas-price volatility can delay a pending `addLiquidity` transaction. Oracle prices in Metric OMM are updated externally; a price update between transaction submission and inclusion is sufficient to trigger the loss. No privileged access is required — the victim is any ordinary LP using the periphery adder.

### Recommendation

Add a `uint256 deadline` parameter to all four public functions in `MetricOmmPoolLiquidityAdder` and call `_checkDeadline(deadline)` (or an equivalent inline check `require(block.timestamp <= deadline)`) at the top of each function, mirroring the pattern already used in `MetricOmmSimpleRouter`.

### Proof of Concept

1. Alice calls `addLiquidityWeighted` with cursor bounds `[bin 0, bin 0]` and `maxAmountToken0 = 1000e18`, `maxAmountToken1 = 1000e18`. The oracle bid/ask at submission time implies a 50/50 token split.
2. The transaction sits in the mempool. The oracle price is updated, moving the mid-price from 1.00 to 1.30 while the cursor remains in bin 0 (no bin crossing, so `CursorOutOfBounds` does not fire).
3. The transaction is included. The probe now sees the new oracle price; `_scaleWeightsToShares` scales shares to the new composition — e.g., 80% token0, 20% token1.
4. Alice's deposit executes at the 1.30 price. Her position immediately has impermanent loss relative to the 1.00 price she intended, with no recourse. [8](#0-7) [9](#0-8)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-167)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L226-243)
```text
  function _scaleWeightsToShares(LiquidityDelta calldata w, uint256 max0, uint256 max1, uint256 need0, uint256 need1)
    internal
    pure
    returns (LiquidityDelta memory out)
  {
    uint256 scaleWad0 = need0 == 0 ? type(uint256).max : Math.mulDiv(max0, WAD, need0);
    uint256 scaleWad1 = need1 == 0 ? type(uint256).max : Math.mulDiv(max1, WAD, need1);
    uint256 scaleWad = scaleWad0 < scaleWad1 ? scaleWad0 : scaleWad1;

    uint256 n = w.binIdxs.length;
    out.binIdxs = new int256[](n);
    out.shares = new uint256[](n);
    for (uint256 i; i < n; i++) {
      out.binIdxs[i] = w.binIdxs[i];
      out.shares[i] = Math.mulDiv(w.shares[i], scaleWad, WAD);
      if (w.shares[i] != 0 && out.shares[i] == 0) revert SharesRoundedToZero();
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

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L1-5)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPoolFactory} from "@metric-core/interfaces/IMetricOmmPoolFactory/IMetricOmmPoolFactory.sol";
import {IMetricOmmSimpleRouter} from "../interfaces/IMetricOmmSimpleRouter.sol";
```
