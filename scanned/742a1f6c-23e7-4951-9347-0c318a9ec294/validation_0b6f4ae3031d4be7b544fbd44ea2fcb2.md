### Title
Missing Deadline Check in `MetricOmmPoolLiquidityAdder` Allows Stale Liquidity Deposits at Unfavorable Prices — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmSimpleRouter` enforces a deadline on every swap entry point via `_checkDeadline`. The sibling periphery contract `MetricOmmPoolLiquidityAdder` exposes four public liquidity-add entry points — two overloads of `addLiquidityExactShares` and two of `addLiquidityWeighted` — none of which accept or enforce a deadline. A pending transaction can sit in the mempool, execute after significant price movement, and deposit the caller's tokens into bins that are now far from the current price, causing a direct loss of LP principal.

---

### Finding Description

`MetricOmmSwapRouterBase._checkDeadline` is defined and called at the top of every swap function in `MetricOmmSimpleRouter`: [1](#0-0) 

```solidity
function _checkDeadline(uint256 deadline) internal view {
    if (block.timestamp > deadline)
        revert IMetricOmmSimpleRouter.TransactionExpired(deadline, block.timestamp);
}
```

It is called at the start of `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`: [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

`MetricOmmPoolLiquidityAdder` has no `deadline` parameter and no `_checkDeadline` call anywhere in its four public entry points: [6](#0-5) [7](#0-6) [8](#0-7) [9](#0-8) 

The `addLiquidityExactShares` overloads have **no time-based protection at all** — only `maxAmountToken0`/`maxAmountToken1` caps enforced in the callback: [10](#0-9) 

The `addLiquidityWeighted` overloads have a cursor-bounds check (`_validateBinAndBinPosition`) that reads `slot0` at probe time, but this is not equivalent to a deadline: the cursor can remain within the user's specified bounds while the pool's actual price composition has shifted enough to produce a materially different deposit ratio. [11](#0-10) 

---

### Impact Explanation

Metric OMM is a bin-based AMM. Each bin holds a single token once the pool cursor has moved past it: bins below the cursor hold only token1; bins above hold only token0; only the active bin holds both. A user who submits `addLiquidityExactShares` targeting bins near the current price can have their transaction delayed in the mempool. If the price moves significantly before inclusion:

- Bins the user targeted are now entirely out-of-range.
- The pool requests only one token from the callback (the single-sided token for those bins).
- The user's tokens are deposited at an effective price that is now stale/unfavorable.
- The user holds LP shares whose value is immediately below the fair-market value of the tokens deposited, constituting a direct loss of principal.

The `maxAmountToken0`/`maxAmountToken1` caps bound the *quantity* of tokens spent but do not protect against *price* at which those tokens are committed. This is the same class of harm as bad-price swap execution.

---

### Likelihood Explanation

- Any user calling `addLiquidityExactShares` directly (not via a private mempool) is exposed.
- Network congestion, gas price spikes, or deliberate transaction ordering by a block builder can delay execution by minutes to hours.
- No privileged actor is required; any unprivileged caller is affected.
- The `addLiquidityWeighted` path is partially mitigated by cursor bounds but remains vulnerable when the user sets wide bounds or when the cursor stays within bounds while the deposit ratio shifts.

---

### Recommendation

Add a `deadline` parameter to all four public entry points in `MetricOmmPoolLiquidityAdder` and call `_checkDeadline(deadline)` as the first statement, mirroring the pattern in `MetricOmmSimpleRouter`:

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
    _validateOwner(owner);
    ...
}
```

Apply the same change to both `addLiquidityWeighted` overloads. The `_checkDeadline` helper already exists in `MetricOmmSwapRouterBase`; `MetricOmmPoolLiquidityAdder` can duplicate the one-liner or extract it to a shared base.

---

### Proof of Concept

1. Pool cursor is at bin 0 (balanced, token0/token1 price = 1:1).
2. Alice calls `addLiquidityExactShares` targeting bins `[-1, 0, 1]` with `maxAmountToken0 = 1000e18`, `maxAmountToken1 = 1000e18`. Transaction enters the mempool.
3. Large swap moves the cursor to bin 5 (price has risen sharply). Bins `[-1, 0, 1]` are now entirely below the cursor and hold only token1.
4. Alice's transaction is included. The pool callback requests `amount0Delta = 0`, `amount1Delta = 3000e18` (all token1, single-sided).
5. Alice's 3000 token1 are deposited at the old price. At the new price, those LP shares are worth materially less than 3000 token1 in fair-market terms.
6. Alice has suffered an immediate loss of principal with no recourse, because no deadline check existed to revert the stale transaction. [12](#0-11) [13](#0-12)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L152-178)
```text
  function metricOmmModifyLiquidityCallback(uint256 amount0Delta, uint256 amount1Delta, bytes calldata callbackData)
    external
    override
  {
    uint8 kind = abi.decode(callbackData, (uint8));
    if (kind == KIND_PROBE) {
      revert LiquidityProbe(amount0Delta, amount1Delta);
    }
    if (kind != KIND_PAY) revert InvalidCallbackKind();

    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
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
