Audit Report

## Title
Missing Slippage Protection When Removing Liquidity - (File: metric-core/contracts/MetricOmmPool.sol)

## Summary
`MetricOmmPool.removeLiquidity()` accepts no `minAmount0Out` / `minAmount1Out` parameters, giving LPs no on-chain mechanism to bound the token amounts they receive. Because payout is computed proportionally from the bin's live `token0BalanceScaled` / `token1BalanceScaled`, a swap that shifts the bin's composition before the LP's transaction lands can reduce one leg to zero with no revert and no recourse. No periphery wrapper for `removeLiquidity` exists, so users must call the core pool directly and are fully exposed.

## Finding Description
`MetricOmmPool.removeLiquidity()` delegates immediately to `LiquidityLib.removeLiquidity()` after an ownership check:

```solidity
// metric-core/contracts/MetricOmmPool.sol L199-212
function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
  external
  nonReentrant(PoolActions.REMOVE_LIQUIDITY)
  returns (uint256 amount0Removed, uint256 amount1Removed)
{
    ...
    (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(...);
    _afterRemoveLiquidity(...);
}
```

No floor check exists anywhere in the call chain. `LiquidityLib.removeLiquidity()` computes each token's share proportionally from the bin's current state and transfers immediately:

```solidity
// metric-core/contracts/libraries/LiquidityLib.sol L205-247
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;
...
IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
```

A swap that exhausts a bin's token1 balance (buying token1 with token0) sets `token1BalanceScaled = 0` in that bin. Any subsequent `removeLiquidity` call for that bin yields `amount1Scaled = 0`, so the LP receives 0 token1 with no revert. The swap math confirms this: `buyToken1InBinSpecifiedIn` decrements `token1BalanceScaled` to zero and increments `token0BalanceScaled` by the input amount. The LP's shares still represent a proportional claim, but now entirely in token0.

A grep across all `metric-periphery/**/*.sol` files returns zero matches for `removeLiquidity`, confirming there is no periphery wrapper. The add-side periphery (`MetricOmmPoolLiquidityAdder`) provides `maxAmountToken0` / `maxAmountToken1` caps enforced in the callback, but no equivalent remove-side entry point exists.

## Impact Explanation
An LP holding shares in the active bin (which holds both tokens) submits `removeLiquidity` expecting a mixed payout. A front-running swap exhausts the bin's token1, converting it to a token0-only bin. The LP's transaction executes: `amount1Removed = 0`, `amount0Removed` equals the full proportional token0 share. The LP receives the full value in a single token they did not want and must swap back at the cost of the protocol's bid/ask spread — a direct, quantifiable loss of LP principal value. On chains with public mempools this is a straightforward sandwich: shift cursor in, let LP withdraw single-sided, shift cursor back.

## Likelihood Explanation
Any LP removing liquidity from the active bin is exposed. The attack requires only a swap large enough to exhaust the active bin's token1 (or token0), which is a normal pool operation available to any unprivileged address. On EVM chains with public mempools (Ethereum mainnet, most L2s) front-running is routine. Natural oracle price movements between block submission and inclusion produce the same outcome without any attacker. Because there is no periphery wrapper, every LP must call the core pool directly and has no mechanism to enforce a minimum.

## Recommendation
Add `minAmount0Out` and `minAmount1Out` to `MetricOmmPool.removeLiquidity()` and revert if the returned amounts fall below them:

```diff
 function removeLiquidity(
     address owner,
     uint80 salt,
     LiquidityDelta calldata deltas,
+    uint256 minAmount0Out,
+    uint256 minAmount1Out,
     bytes calldata extensionData
 ) external nonReentrant(PoolActions.REMOVE_LIQUIDITY)
   returns (uint256 amount0Removed, uint256 amount1Removed)
 {
     ...
     (amount0Removed, amount1Removed) = LiquidityLib.removeLiquidity(...);
+    if (amount0Removed < minAmount0Out || amount1Removed < minAmount1Out)
+        revert InsufficientOutput(amount0Removed, amount1Removed, minAmount0Out, minAmount1Out);
     _afterRemoveLiquidity(...);
 }
```

Additionally, add a `removeLiquidity` wrapper to `MetricOmmPoolLiquidityAdder` that forwards these bounds, mirroring the `maxAmountToken0` / `maxAmountToken1` pattern already present for `addLiquidityExactShares`.

## Proof of Concept
1. Pool deployed with bins `[-1, 0, 1]`; oracle cursor at bin 0 (active bin holds both tokens).
2. LP calls `pool.addLiquidity(owner, salt, deltas_bin0, ...)` depositing 100 token0 + 100 token1 into bin 0.
3. LP submits `pool.removeLiquidity(owner, salt, deltas_bin0, "")` expecting ≈100 token0 + ≈100 token1.
4. Attacker front-runs with a `zeroForOne` swap large enough to exhaust bin 0's token1 balance. `buyToken1InBinSpecifiedIn` sets `binState.token1BalanceScaled = 0` and increases `binState.token0BalanceScaled`.
5. LP's `removeLiquidity` executes: `amount1Scaled = _checkedMul(0, sharesToRemove) / binTotalSharesVal = 0`. LP receives only token0.
6. Attacker back-runs with the reverse swap, restoring the cursor.
7. LP received 0 token1 with no revert, no recourse, and must swap back at spread cost.

**Foundry test sketch:**
```solidity
function test_removeLiquidity_noSlippageProtection() public {
    // Setup: add liquidity to active bin
    pool.addLiquidity(lp, salt, deltas_bin0, callbackData, "");
    // Attacker exhausts token1 in bin 0
    pool.swap(attacker, true, largeAmount, minPrice, callbackData, "");
    // LP removes liquidity — receives 0 token1, no revert
    (uint256 a0, uint256 a1) = pool.removeLiquidity(lp, salt, deltas_bin0, "");
    assertEq(a1, 0); // LP got zero token1 with no protection
}
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-81)
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
