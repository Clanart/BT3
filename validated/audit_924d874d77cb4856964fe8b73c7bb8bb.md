### Title
Missing Deadline Parameter in Liquidity Adder Allows Stale Execution at Unintended Oracle Price — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` exposes four public liquidity-addition entry points — two overloads of `addLiquidityExactShares` and two overloads of `addLiquidityWeighted` — none of which accept or enforce a `deadline` timestamp. The sibling `MetricOmmSimpleRouter` correctly calls `_checkDeadline` on every swap entry point, but the liquidity adder has no equivalent guard. A transaction that sits in the mempool and executes after the oracle price has moved will deposit the user's tokens at a price composition that was never intended, causing immediate, unrecoverable impermanent loss relative to the user's original intent.

---

### Finding Description

`MetricOmmSimpleRouter` enforces a deadline on every swap path: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

`MetricOmmPoolLiquidityAdder` has no such check on any of its four public entry points: [5](#0-4) [6](#0-5) 

The `addLiquidityWeighted` flow is particularly sensitive: it first runs a **probe** call to determine the current oracle-price-driven token ratio (`need0`, `need1`), then scales the user's weight vector to that ratio and executes the paying deposit. [7](#0-6) 

If the transaction is delayed and the oracle price shifts within the user's cursor bounds, the probe runs at the **new** price, producing a completely different `need0`/`need1` ratio. The user's tokens are then deposited at that new ratio — not the ratio they observed when they signed the transaction.

The `_validateBinAndBinPosition` guard only reverts if the cursor moves **outside** the user-supplied `[minimalCurBin, maximalCurBin]` window: [8](#0-7) 

Any price movement that keeps the cursor inside that window — which is the common case for a user who sets a reasonable range — passes validation and executes at the stale composition.

---

### Impact Explanation

**Medium.** The user's token spend is bounded by `maxAmountToken0` / `maxAmountToken1`, so there is no unbounded drain. However, the composition of the deposit (how much token0 vs. token1 is taken) is determined by the oracle price at execution time, not at signing time. A significant price move within the cursor window causes the user to:

1. Receive LP shares concentrated around the **wrong** oracle price.
2. Suffer immediate impermanent loss relative to their intended position.
3. Have no recourse — the liquidity is already deposited and the loss is locked in.

This is a direct loss of LP asset value above dust thresholds for any non-trivial deposit size.

---

### Likelihood Explanation

**Low.** Requires the user to submit with a low gas price, the transaction to remain pending long enough for the oracle price to move materially, and the cursor to stay within the user's specified bounds. These conditions are uncommon but realistic during periods of network congestion or volatile markets.

---

### Recommendation

Add a `deadline` parameter to all four public entry points of `MetricOmmPoolLiquidityAdder` and call the same `_checkDeadline` helper used by the router at the top of each function, before any state reads or pool calls.

```solidity
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
+   uint256 deadline,
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
+   _checkDeadline(deadline);
    ...
}
```

Apply the same pattern to both `addLiquidityExactShares` overloads and both `addLiquidityWeighted` overloads.

---

### Proof of Concept

1. Alice calls `addLiquidityWeighted` when ETH/USDC oracle price is $2 000. She sets `maxAmountToken0 = 1e18` (1 ETH), `maxAmountToken1 = 2 000e6` (2 000 USDC), and a cursor window of `[-10, 10]`.
2. The transaction sits in the mempool for several hours due to low gas.
3. The oracle price drops to $1 500 (cursor stays within `[-10, 10]`).
4. The transaction executes. The probe runs at $1 500 and returns `need0 = 1e18`, `need1 = 1 500e6`.
5. `_scaleWeightsToShares` scales to fit within Alice's max caps. The paying deposit pulls ~1 ETH and ~1 500 USDC.
6. Alice's LP position is now centred around $1 500. If ETH recovers to $2 000, she suffers impermanent loss compared to the position she intended to open at $2 000.
7. No deadline check exists to revert the transaction before step 4. [9](#0-8)

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
