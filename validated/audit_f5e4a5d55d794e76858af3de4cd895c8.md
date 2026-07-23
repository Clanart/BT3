After a thorough search of all periphery and core contracts, I found the following:

- `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` in `MetricOmmSimpleRouter` all have slippage guards (`amountOutMinimum` / `amountInMaximum`). [1](#0-0) 
- `addLiquidityExactShares` and `addLiquidityWeighted` in `MetricOmmPoolLiquidityAdder` enforce `maxAmountToken0` / `maxAmountToken1` caps. [2](#0-1) 
- **No periphery wrapper exists for `removeLiquidity`**; users call the pool directly. [3](#0-2) 
- The pool's `removeLiquidity` accepts no minimum-amount-out parameters and transfers whatever the current bin state dictates. [4](#0-3) 

---

### Title
Missing Slippage Protection on `removeLiquidity()` Exposes LPs to Receiving Less Than Simulated — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary
`MetricOmmPool.removeLiquidity` has no `minAmount0Out` / `minAmount1Out` guard. The periphery layer (`MetricOmmPoolLiquidityAdder`) provides no wrapper for removal, so LPs must call the pool directly. Between the moment an LP reads bin state off-chain to estimate their proceeds and the moment their transaction executes, swaps can shift the bin's token0/token1 composition, causing the LP to receive a materially different (and potentially lower-value) token mix with no on-chain recourse.

### Finding Description
`MetricOmmPool.removeLiquidity` computes the LP's share of each bin's current `token0BalanceScaled` and `token1BalanceScaled`, then transfers the result directly to `owner`:

```solidity
// metric-core/contracts/MetricOmmPool.sol  lines 199-212
function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)
    returns (uint256 amount0Removed, uint256 amount1Removed)
{
    ...
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(...);
    _afterRemoveLiquidity(...);
}
``` [4](#0-3) 

Inside `LiquidityLib.removeLiquidity`, the amounts are computed proportionally and transferred with no floor check:

```solidity
// metric-core/contracts/libraries/LiquidityLib.sol  lines 205-246
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
...
IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
``` [5](#0-4) 

The function signature accepts no `minAmount0Out` or `minAmount1Out`:

```solidity
function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external returns (uint256 amount0Removed, uint256 amount1Removed);
``` [6](#0-5) 

The periphery `MetricOmmPoolLiquidityAdder` exposes only add-liquidity entry points; there is no periphery wrapper for removal that could add a minimum-amount guard: [3](#0-2) 

### Impact Explanation
An LP reads bin state off-chain (or via `eth_call`) to estimate `amount0` and `amount1` they will receive. One or more swaps execute before their `removeLiquidity` transaction lands, shifting the bin's token composition. The LP receives a different (and potentially lower total-value) mix of tokens with no ability to revert. This is a direct loss of owed LP assets — the same class of harm as the reference `sellVotes()` finding.

### Likelihood Explanation
The Metric OMM pool is oracle-anchored, so oracle price updates trigger swap activity continuously. Any LP who previews a removal and submits the transaction in a separate block faces this window. No privileged access is required; any LP with an existing position is exposed on every removal.

### Recommendation
Add `minAmount0Out` and `minAmount1Out` parameters to `IMetricOmmPoolActions.removeLiquidity` and enforce them after the amounts are computed:

```solidity
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    uint256 minAmount0Out,   // <-- add
    uint256 minAmount1Out,   // <-- add
    bytes calldata extensionData
) external returns (uint256 amount0Removed, uint256 amount1Removed);
```

Inside `LiquidityLib.removeLiquidity`, after computing `amount0Removed` and `amount1Removed`, revert if either is below the caller's floor:

```solidity
if (amount0Removed < minAmount0Out || amount1Removed < minAmount1Out)
    revert InsufficientOutput(amount0Removed, amount1Removed, minAmount0Out, minAmount1Out);
```

Alternatively, add a periphery `removeLiquidity` wrapper in `MetricOmmPoolLiquidityAdder` that reads the returned amounts and reverts on the caller's behalf, keeping the pool interface unchanged.

### Proof of Concept
1. LP holds 10 000 shares in bin 0 (the active bin). Off-chain read shows bin 0 holds 1 000 token0 and 0 token1; LP expects to receive ~1 000 token0.
2. Before the LP's `removeLiquidity` transaction lands, a large swap sells token0 into the pool, shifting bin 0 to hold 500 token0 and 500 token1 (in oracle-value terms).
3. `removeLiquidity` executes: LP receives 500 token0 and 500 token1. If the LP only wanted token0 (e.g., to repay a debt denominated in token0), they are short 500 token0 with no revert path.
4. No guard in `removeLiquidity` or any periphery wrapper can prevent this outcome under the current interface.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L83-83)
```text
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L165-166)
```text
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
```

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

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L205-247)
```text
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;

          // casting to uint104 is safe because amount0Scaled and amount1Scaled are less than token(0|1)BalanceScaled
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token0BalanceScaled -= uint104(amount0Scaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token1BalanceScaled -= uint104(amount1Scaled);
          binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
          positionBinShares[posKey] = newUserShares;

          totalToken0ToRemoveScaled += amount0Scaled;
          totalToken1ToRemoveScaled += amount1Scaled;

          binBalanceDeltas[i] = BinBalanceDelta({
            // safe because amount0Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta0Scaled: -int256(amount0Scaled),
            // safe because amount1Scaled is bounded by uint104 bin balances.
            // forge-lint: disable-next-line(unsafe-typecast)
            delta1Scaled: -int256(amount1Scaled)
          });
        }
      }

      if (totalToken0ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken0 = uint128(uint256(binTotals.scaledToken0) - totalToken0ToRemoveScaled);
      }
      if (totalToken1ToRemoveScaled > 0) {
        // forge-lint: disable-next-line(unsafe-typecast)
        binTotals.scaledToken1 = uint128(uint256(binTotals.scaledToken1) - totalToken1ToRemoveScaled);
      }

      (amount0Removed, amount1Removed) =
        _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }
```

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L172-174)
```text
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    returns (uint256 amount0Removed, uint256 amount1Removed);
```
