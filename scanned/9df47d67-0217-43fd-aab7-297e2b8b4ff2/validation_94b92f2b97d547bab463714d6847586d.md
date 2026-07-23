### Title
Missing deadline enforcement in `MetricOmmPoolLiquidityAdder` allows stale LP transactions to execute at unintended oracle prices — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` exposes four `addLiquidity*` entry points, none of which accept or enforce a `deadline` parameter. A pending transaction can sit in the mempool indefinitely and execute at a future oracle price the LP never approved, causing the LP to receive a position with a different token-ratio composition than intended. The swap router (`MetricOmmSimpleRouter`) correctly calls `_checkDeadline` on every entry point; the liquidity adder has no equivalent guard.

---

### Finding Description

`MetricOmmSwapRouterBase._checkDeadline` is defined and called at the top of every swap entry point in `MetricOmmSimpleRouter`:

```
exactInputSingle  → _checkDeadline(params.deadline)   // line 68
exactInput        → _checkDeadline(params.deadline)   // line 93
exactOutputSingle → _checkDeadline(params.deadline)   // line 131
exactOutput       → _checkDeadline(params.deadline)   // line 155
``` [1](#0-0) 

None of the four `addLiquidity*` functions in `MetricOmmPoolLiquidityAdder` accept a `deadline` argument or perform any timestamp check: [2](#0-1) [3](#0-2) 

Because Metric OMM is oracle-anchored, the pool's bid/ask prices at execution time are set by the live oracle feed, not by the LP's deposit ratio. If a transaction is delayed (low gas, mempool congestion, deliberate withholding by a searcher), the oracle price at execution can differ substantially from the price at signing time. The LP's `maxAmountToken0`/`maxAmountToken1` caps bound the *quantity* of tokens pulled but do not bound the *price* at which those tokens are deployed. The LP ends up with a position whose bin composition reflects the oracle price at execution, not the price they evaluated when constructing the transaction.

For `addLiquidityExactShares` there is no cursor-bound protection at all: [2](#0-1) 

`addLiquidityWeighted` has cursor bounds (`minimalCurBin`/`maximalCurBin`) that can partially mitigate this, but they are optional (callers may pass `type(int8).min`/`type(int8).max`) and do not substitute for a deadline: [4](#0-3) 

---

### Impact Explanation

**Medium.** An LP who submits `addLiquidityExactShares` at oracle price P₀ may have the transaction executed at oracle price P₁ ≫ P₀ (or ≪ P₀). The pool allocates the LP's tokens across bins at P₁'s composition. The LP's intended exposure (e.g., equal-value token0/token1 split) is silently replaced by a skewed position. Because the oracle price is always current, arbitrageurs do not immediately drain the pool, but the LP holds a position they did not consent to. The `maxAmountToken0`/`maxAmountToken1` caps prevent overpayment in quantity but do not prevent wrong-price deployment of capital.

---

### Likelihood Explanation

**Low-to-Medium.** Requires a transaction to remain pending long enough for the oracle price to move materially. This is plausible during network congestion or when a searcher deliberately delays inclusion. The `addLiquidityExactShares` path (no cursor bounds) is the most exposed variant.

---

### Recommendation

Add a `uint256 deadline` parameter to all four `addLiquidity*` signatures in `IMetricOmmPoolLiquidityAdder` and `MetricOmmPoolLiquidityAdder`, and enforce it at the top of each function using the same `_checkDeadline` pattern already present in `MetricOmmSwapRouterBase`:

```solidity
// at the top of addLiquidityExactShares / addLiquidityWeighted
if (block.timestamp > deadline) revert TransactionExpired(deadline, block.timestamp);
```

This mirrors the fix applied in the referenced ERC-7683 report and is consistent with how `MetricOmmSimpleRouter` already protects swap callers.

---

### Proof of Concept

1. LP calls `addLiquidityExactShares(pool, owner, salt, deltas, 1000e6 /*USDC*/, 1e18 /*ETH*/, "")` when oracle reports ETH = $1 000. Transaction is submitted with a low gas price.
2. Network congestion holds the transaction for 30 minutes. Oracle price updates to ETH = $2 000.
3. Transaction executes. The pool's active bin cursor has shifted; the pool now requests ~500 USDC + 1 ETH (or 1000 USDC + 0.5 ETH) to match the new oracle price. Both amounts are within the LP's caps, so the callback succeeds.
4. LP receives a position at $2 000/ETH instead of $1 000/ETH — a composition they never approved and cannot revert.
5. No revert occurs anywhere in the call chain because `MetricOmmPoolLiquidityAdder` performs no timestamp check. [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L152-179)
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
