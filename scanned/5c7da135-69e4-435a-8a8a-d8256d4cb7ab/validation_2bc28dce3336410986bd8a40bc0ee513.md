### Title
Missing Deadline Check in `MetricOmmPoolLiquidityAdder` Allows Stale Liquidity Additions at Unfavorable Prices — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

All four public liquidity-adding functions in `MetricOmmPoolLiquidityAdder` accept no `deadline` parameter and perform no `block.timestamp` guard. A pending transaction can be held in the mempool and executed arbitrarily far in the future at pool conditions the user never intended to accept.

---

### Finding Description

`MetricOmmSimpleRouter` correctly calls `_checkDeadline(params.deadline)` at the top of every swap entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`). [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

`MetricOmmPoolLiquidityAdder` has no equivalent guard in any of its four public entry points:

- `addLiquidityExactShares(pool, owner, salt, deltas, maxAmountToken0, maxAmountToken1, extensionData)` [5](#0-4) 
- `addLiquidityExactShares(pool, salt, deltas, maxAmountToken0, maxAmountToken1, extensionData)` [6](#0-5) 
- `addLiquidityWeighted(pool, owner, salt, weightDeltas, maxAmountToken0, maxAmountToken1, minimalCurBin, minimalPosition, maximalCurBin, maximalPosition, extensionData)` [7](#0-6) 
- `addLiquidityWeighted(pool, salt, weightDeltas, ...)` [8](#0-7) 

None of these functions accept or validate a `deadline`. The `maxAmountToken0` / `maxAmountToken1` caps prevent the user from paying *more* than intended, but they do not prevent the transaction from executing at a future time when the pool price has moved far from the user's intended entry point.

For `addLiquidityWeighted` the probe step reads the live pool cursor at execution time: [9](#0-8) 

The `_validateBinAndBinPosition` check provides a coarse cursor-range guard, but it only reverts if the cursor has moved *outside* the user-supplied `[minimalCurBin, maximalCurBin]` window. A transaction delayed by hours or days can still execute anywhere inside that window at a price the user never intended to accept, and the resulting position composition will reflect the stale pool state, not the user's original intent.

For `addLiquidityExactShares` there is no cursor-range guard at all — the transaction executes unconditionally regardless of how much time has elapsed or how far the price has moved.

---

### Impact Explanation

A user who submits a liquidity-add during a gas-price spike may have their transaction mined much later at a materially different price. Their tokens are deposited into bins at the wrong price level. The `maxAmount` caps bound the token quantity spent but not the economic value of the resulting position. In a volatile market the position can be immediately deep out-of-range, effectively locking principal at a loss relative to simply holding the tokens.

This is a direct loss of user principal above Sherlock medium thresholds: the user receives a liquidity position worth less than the tokens they deposited, with no recourse.

---

### Likelihood Explanation

Any period of mempool congestion (gas-price spike, network stress) causes transactions to queue. This is a routine occurrence on mainnet. No special attacker capability is required — a searcher or validator can simply delay inclusion. The user has no on-chain mechanism to cancel or protect themselves once the transaction is broadcast.

---

### Recommendation

Add a `uint256 deadline` parameter to all four public functions and revert if `block.timestamp > deadline`, mirroring the pattern already used in `MetricOmmSimpleRouter`:

```solidity
// in MetricOmmPoolLiquidityAdder
error DeadlineExpired(uint256 deadline, uint256 current);

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
+   if (block.timestamp > deadline) revert DeadlineExpired(deadline, block.timestamp);
    _validateOwner(owner);
    ...
}
```

Apply the same change to both `addLiquidityWeighted` overloads and the second `addLiquidityExactShares` overload.

---

### Proof of Concept

1. User calls `addLiquidityExactShares` targeting bin index 0 (current price) with `maxAmountToken0 = 1000e18`, `maxAmountToken1 = 1000e18`.
2. Transaction is broadcast but not mined due to low gas price.
3. Pool price moves: bin cursor shifts to bin index +5 (token0 now much cheaper).
4. Transaction is mined hours later. The pool accepts the exact shares at bin 0, which is now deep out-of-range on the token0 side.
5. User's 1000e18 token0 is deposited into a bin that will never be crossed again at current prices. The position is worth significantly less than the deposited tokens.
6. No revert occurs because `maxAmountToken0` and `maxAmountToken1` were not exceeded — the pool simply consumed the tokens at the stale bin. [5](#0-4)

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
