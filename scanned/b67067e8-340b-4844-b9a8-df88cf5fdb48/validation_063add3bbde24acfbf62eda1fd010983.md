### Title
LP `removeLiquidity` Has No Minimum-Output Slippage Guard, Enabling Silent Fund Loss - (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.removeLiquidity()` accepts no `minAmount0Out` / `minAmount1Out` parameters, and the periphery `MetricOmmPoolLiquidityAdder` provides **no `removeLiquidity` wrapper at all**. An LP who previews expected withdrawal amounts and then submits a `removeLiquidity` transaction can silently receive far less than expected if swaps drain the relevant bins before their transaction executes.

---

### Finding Description

The pool's `removeLiquidity` function signature is:

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,   // bin indices + shares to burn
    bytes calldata extensionData
) external ... returns (uint256 amount0Removed, uint256 amount1Removed)
``` [1](#0-0) 

There are no `minAmount0` or `minAmount1` parameters. The returned amounts are computed from the live `binTotals.scaledToken0` / `binTotals.scaledToken1` at execution time, which are mutated by every swap that crosses those bins. [2](#0-1) 

The periphery `MetricOmmPoolLiquidityAdder` only exposes `addLiquidityExactShares` and `addLiquidityWeighted` — both with `maxAmountToken0` / `maxAmountToken1` caps — but **has no `removeLiquidity` entry point at all**. [3](#0-2) 

This means every LP must call `removeLiquidity` directly on the pool, with zero slippage protection.

---

### Impact Explanation

An LP previews their expected withdrawal (e.g., via a lens/quoter call or off-chain simulation). Between that preview and the on-chain execution, one or more swaps execute and drain token0 or token1 from the bins the LP holds shares in. The LP's `removeLiquidity` succeeds but returns amounts far below what was previewed, with no revert to protect them. The loss is proportional to the swap volume that precedes the removal in the same block.

This is a **direct loss of LP principal** with no on-chain guard to prevent it.

---

### Likelihood Explanation

- Any LP removing liquidity from an active pool faces this risk on every withdrawal.
- No special attacker is required; ordinary swap activity in the same block is sufficient.
- The pool is oracle-anchored and designed for high-frequency trading, making concurrent swap activity the normal case, not an edge case.
- The periphery adder's absence of a `removeLiquidity` wrapper means there is no path for an LP to add slippage protection without deploying their own contract.

---

### Recommendation

Add `minAmount0Out` and `minAmount1Out` parameters to `MetricOmmPool.removeLiquidity()`, reverting if the computed amounts fall below the caller's minimums:

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 minAmount0Out,   // <-- add
    uint256 minAmount1Out,   // <-- add
    bytes calldata extensionData
) external returns (uint256 amount0Removed, uint256 amount1Removed) {
    ...
    if (amount0Removed < minAmount0Out || amount1Removed < minAmount1Out)
        revert InsufficientOutput(amount0Removed, amount1Removed, minAmount0Out, minAmount1Out);
}
```

Alternatively, add a `removeLiquidity` wrapper to `MetricOmmPoolLiquidityAdder` that performs the check after the pool call returns, analogous to how `exactInputSingle` checks `amountOutMinimum` after the swap. [4](#0-3) 

---

### Proof of Concept

**Textual PoC:**

1. LP Alice holds shares in bin 0 of a pool. She calls a quoter/view and learns she will receive 1000 token0 and 1000 token1 for her shares.
2. Alice submits `removeLiquidity(alice, salt, deltas, "")` to the mempool.
3. Before Alice's transaction executes, a large swap (token1 → token0) drains most of token0 from bin 0, updating `binTotals.scaledToken0` downward.
4. Alice's `removeLiquidity` executes. Her shares now entitle her to only 200 token0 and 1800 token1 (same dollar value only if the oracle price is exactly right, but in practice the composition shift causes a loss relative to her expected balanced withdrawal).
5. The transaction succeeds silently. Alice has no recourse.

**Code path:**

- `MetricOmmPool.removeLiquidity` → `LiquidityLib.removeLiquidity` computes amounts from live `binTotals` and per-bin balances with no floor check.
- No periphery wrapper exists to add a post-call minimum check. [5](#0-4)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L199-212)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
  {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    if (msg.sender != owner) revert NotPositionOwner();
    _beforeRemoveLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(
      _liquidityContext(), owner, salt, deltas, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterRemoveLiquidity(msg.sender, owner, salt, deltas, amount0Removed, amount1Removed, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L732-748)
```text
      if (zeroForOne) {
        // casting to uint256 is safe because amount0DeltaScaled is positive in zeroForOne flow.
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken0 =
          (uint256(binTotals.scaledToken0) + uint256(amount0DeltaScaled) - protocolFeeScaled).toUint128(); // forge-lint: disable-line(unsafe-typecast)
        // casting to uint128/uint256 is safe because bin totals remain bounded by uint128-scaled accounting invariants.
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 = uint128(uint256(binTotals.scaledToken1) - uint256(-amount1DeltaScaled));
      } else {
        // casting to uint256 is safe because amount1DeltaScaled is positive in !zeroForOne flow.
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 =
          (uint256(binTotals.scaledToken1) + uint256(amount1DeltaScaled) - protocolFeeScaled).toUint128(); // forge-lint: disable-line(unsafe-typecast)
        // casting to uint128/uint256 is safe because bin totals remain bounded by uint128-scaled accounting invariants.
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken0 = uint128(uint256(binTotals.scaledToken0) - uint256(-amount0DeltaScaled));
      }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L49-81)
```text
  // ============ External: liquidity ============

  /// @notice Add liquidity with explicit per-bin shares; reverts in callback if token amounts exceed caps.
  /// @dev `msg.sender` is always the payer for token pulls in callback (stored in transient settlement context).
  /// @param owner Position owner recorded by the pool.
  /// @param maxAmountToken0 Max token0 (native units) the pool may request; inclusive check before pull.
  /// @param maxAmountToken1 Max token1 (native units) the pool may request; inclusive check before pull.
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

  /// @notice Add liquidity with explicit per-bin shares for `msg.sender`.
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L81-83)
```text
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
```
