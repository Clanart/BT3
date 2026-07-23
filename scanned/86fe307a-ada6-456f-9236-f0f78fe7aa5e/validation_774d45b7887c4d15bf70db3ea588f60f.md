### Title
Missing Deadline Check on All Liquidity-Addition Entry Points Allows Stale Execution at Unfavorable Oracle Price - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

### Summary
`MetricOmmPoolLiquidityAdder` exposes four public liquidity-addition functions — two overloads of `addLiquidityExactShares` and two of `addLiquidityWeighted` — none of which accept or enforce a `deadline` parameter. A user's pending transaction can sit in the mempool and execute long after the oracle price has moved, causing the pool to pull tokens at a composition the user never intended and inflicting a direct loss of principal.

### Finding Description
Every swap entry point in `MetricOmmSimpleRouter` calls `_checkDeadline(params.deadline)` as its first action: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

The helper reverts when `block.timestamp > deadline`: [5](#0-4) 

`MetricOmmPoolLiquidityAdder` has no equivalent guard. Its four public entry points carry no `deadline` parameter and perform no timestamp check before pulling tokens from the user: [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) 

Metric OMM is an oracle-anchored AMM: the pool's active bin and token composition are determined by the external oracle price at execution time. When a liquidity-addition transaction is delayed in the mempool and the oracle price moves, the pool cursor shifts to a different bin. The user's shares are then deposited into bins at the new price, pulling a token composition that may be entirely different from what the user intended at signing time.

The `addLiquidityWeighted` overloads include a cursor-bounds guard (`_validateBinAndBinPosition`), but this only reverts if the cursor has moved outside an explicit range supplied by the caller: [10](#0-9) 

If the caller omits tight bounds (or uses `addLiquidityExactShares`, which has no cursor check at all), the transaction executes at whatever price the oracle reports at inclusion time.

### Impact Explanation
A user who submits an `addLiquidityExactShares` or `addLiquidityWeighted` transaction during a period of low gas prices may have it mined minutes or hours later. In that window the oracle price can move substantially. The pool will pull up to `maxAmountToken0` / `maxAmountToken1` of the user's tokens at the new, unfavorable composition. The user receives LP shares whose underlying value is less than the tokens deposited — a direct, immediate loss of principal. The loss is bounded by the user's own caps but can be significant for large positions or volatile assets.

### Likelihood Explanation
Any network congestion event (gas spike, block reorg, sequencer delay on L2) can delay a pending transaction. Oracle-anchored pools are specifically designed to track external prices rapidly, so even a short delay can produce a meaningful cursor shift. No privileged access is required; any ordinary user calling the liquidity adder is exposed.

### Recommendation
Add a `uint256 deadline` parameter to all four public entry points of `MetricOmmPoolLiquidityAdder` and call the same `_checkDeadline` helper used by the router before any state-changing logic:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    uint256 deadline,          // ← add
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _checkDeadline(deadline);  // ← add
    ...
}
```

Apply the same pattern to both `addLiquidityWeighted` overloads. This mirrors the protection already present in every swap entry point and is consistent with the protocol's own design intent.

### Proof of Concept

1. Alice calls `addLiquidityExactShares` with `maxAmountToken0 = 10_000e18`, `maxAmountToken1 = 10_000e18` when the oracle price implies a 50/50 split.
2. The transaction is submitted with a low gas price and sits in the mempool for 30 minutes.
3. The oracle price moves 20 % in favour of token1; the pool cursor shifts several bins.
4. The transaction is mined. The pool now requests `~0 token0` and `~10_000e18 token1` (the full cap of the now-cheaper leg).
5. Alice's LP position is worth materially less than the `~10_000e18 token1` she deposited, because the oracle-anchored pool immediately marks her shares at the new price.
6. No revert occurs; Alice has no recourse. [6](#0-5) [11](#0-10)

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

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L91-94)
```text
  function _checkDeadline(uint256 deadline) internal view {
    // forge-lint: disable-next-line(block-timestamp)
    if (block.timestamp > deadline) revert IMetricOmmSimpleRouter.TransactionExpired(deadline, block.timestamp);
  }
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
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
