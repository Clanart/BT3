### Title
Missing Deadline Parameter in `addLiquidityExactShares` Allows Stale Liquidity Deposits at Unfavorable Oracle Prices - (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder.addLiquidityExactShares` lacks a `deadline` parameter. A pending transaction can execute long after submission, when the oracle-anchored pool price has moved significantly, causing the user to deposit liquidity into bins that are immediately out-of-range and subject to instant impermanent loss.

---

### Finding Description

All four swap entry points in `MetricOmmSimpleRouter` (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) call `_checkDeadline(params.deadline)` at entry. The liquidity adder provides no equivalent guard.

Both overloads of `addLiquidityExactShares` accept a caller-specified set of `binIdxs` and `shares` that were chosen relative to the oracle price at the time the user signed and submitted the transaction:

```solidity
// metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol L56-68
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

There is no `deadline` field and no cursor-position guard (unlike `addLiquidityWeighted`, which at least validates `minimalCurBin`/`maximalCurBin`). The only protection is `maxAmountToken0`/`maxAmountToken1`, which caps how much the user pays but does **not** prevent execution at a stale price.

If the transaction sits in the mempool while the oracle price drifts, the pool's bin cursor moves. When the transaction finally lands:

1. The token amounts required for the user's chosen bins may still fall within the caps (so the caps do not revert the call).
2. The bins the user targeted are now far from the current oracle mid-price.
3. The user's liquidity is immediately one-sided and exposed to full impermanent loss on the side that is now in-range.

---

### Impact Explanation

An LP who submits `addLiquidityExactShares` targeting bins near the current oracle price can have their deposit executed minutes or hours later at a materially different price. Because Metric OMM is oracle-anchored, the pool's active bin moves with the oracle; bins that were near mid-price at submission time can be deep out-of-range at execution time. The deposited tokens are immediately worth less than the equivalent spot value, constituting a direct loss of user principal with no recovery path from the contract.

Severity: **Medium** — requires only network congestion or a deliberate delay (e.g., a searcher withholding the transaction until a favorable oracle move); no privileged access is needed.

---

### Likelihood Explanation

Any user interacting with `addLiquidityExactShares` during periods of oracle volatility or mempool congestion is at risk. The attack requires no special permissions: a searcher can observe a pending liquidity transaction and delay its inclusion (e.g., by front-running with a higher-gas transaction that fills the block) until the oracle price has moved enough to make the deposit unfavorable. This is a standard MEV vector.

---

### Recommendation

Add a `uint256 deadline` parameter to both `addLiquidityExactShares` overloads and both `addLiquidityWeighted` overloads, and revert if `block.timestamp > deadline`, mirroring the pattern already used in `MetricOmmSimpleRouter`:

```solidity
function addLiquidityExactShares(
    address pool,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    uint256 deadline,          // <-- add
    bytes calldata extensionData
) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _checkDeadline(deadline);  // <-- add
    _validateOwner(owner);
    ...
}
```

The `_checkDeadline` helper already exists in `MetricOmmSwapRouterBase` and can be inherited or duplicated.

---

### Proof of Concept

1. Oracle price for token0/token1 is $2 000 at block N. User constructs a `LiquidityDelta` targeting bins [−1, 0, +1] around the current cursor and submits `addLiquidityExactShares` with `maxAmountToken0 = 1 000e6`, `maxAmountToken1 = 0.5e18`.
2. Network is congested; the transaction is not mined until block N+200 (~40 minutes on a 12-second chain).
3. By block N+200 the oracle price has moved to $1 600. The pool cursor has shifted; bins [−1, 0, +1] relative to the old cursor are now 3–4 bins below the new cursor.
4. `addLiquidityExactShares` executes. The required token amounts for those stale bins are still within the caps (e.g., `amount0Delta = 800e6 < 1 000e6`), so the call succeeds.
5. The user's liquidity sits entirely below the current oracle price. It is immediately one-sided in token0 and worth ~20% less than the deposited value at spot, with no mechanism in the contract to revert or compensate. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-68)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
```
