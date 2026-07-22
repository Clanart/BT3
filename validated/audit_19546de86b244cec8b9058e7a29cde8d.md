### Title
LP Burns Non-Zero Shares But Receives Zero Tokens Due to Scaled-to-Native Floor Rounding in `removeLiquidity` — (File: metric-core/contracts/libraries/LiquidityLib.sol)

---

### Summary

`LiquidityLib.removeLiquidity` permanently burns an LP's shares and decrements the pool's internal scaled balances, but then converts the scaled withdrawal amount to native tokens using **floor division**. When the LP's proportional scaled claim is smaller than the pool's `token*ScaleMultiplier`, the floor rounds the native transfer to zero. The LP loses their principal with no recourse.

---

### Finding Description

The burn path in `removeLiquidity` executes in two distinct phases that are not atomically consistent:

**Phase 1 — State mutation (lines 205–214):** The LP's proportional scaled claim is computed with floor division, then the bin's scaled balances and the LP's share record are permanently updated:

```solidity
uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;

binState.token0BalanceScaled -= uint104(amount0Scaled);
binState.token1BalanceScaled -= uint104(amount1Scaled);
binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
positionBinShares[posKey] = newUserShares;          // shares burned — irreversible
```

**Phase 2 — Native transfer (lines 239–247):** The accumulated scaled amounts are converted to native token units using a second floor division through `_deltasScaledToExternal`:

```solidity
(amount0Removed, amount1Removed) =
    _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

if (amount0Removed > 0) { IERC20(ctx.token0).safeTransfer(owner, amount0Removed); }
if (amount1Removed > 0) { IERC20(ctx.token1).safeTransfer(owner, amount1Removed); }
```

Inside `_deltasScaledToExternal` with `Math.Rounding.Floor`:

```solidity
deltaAmount0 = scaledDeltaAmount0 / ctx.token0ScaleMultiplier;
deltaAmount1 = scaledDeltaAmount1 / ctx.token1ScaleMultiplier;
```

The `token*ScaleMultiplier` is `10^(max(18, token0Decimals, token1Decimals) − tokenDecimals)`. For a USDC (6 dec) / WETH (18 dec) pool, `token0ScaleMultiplier = 10^12`. Whenever `totalToken0ToRemoveScaled < 10^12`, `amount0Removed = 0` and **no USDC is transferred**, even though the LP's shares have already been erased from storage.

The `addLiquidity` path deliberately uses `Math.Rounding.Ceil` for the same conversion (line 142), so the pool always collects at least as many native tokens as its scaled credit. The remove path has no symmetric protection: it uses `Math.Rounding.Floor` and has no guard that reverts when the resulting native amount is zero despite a non-zero scaled claim.

The `MinimalLiquidity` guard (line 200–202) only prevents leaving a *surviving* dust position; it does not prevent a full burn that yields zero native tokens.

---

### Impact Explanation

**Severity: High — direct loss of LP principal.**

An LP who calls `removeLiquidity` with a valid, non-dust share count can have their entire position erased from pool storage while receiving **zero tokens** in return. The forfeited scaled balance remains inside the pool and is silently redistributed to all remaining LPs through the unchanged `binTotalShares` denominator. This is a permanent, irreversible loss of user funds with no recovery path.

---

### Likelihood Explanation

The condition is reachable on any pool whose two tokens have different decimal counts — the most common real-world pairing (e.g., USDC 6 dec / WETH 18 dec, USDT 6 dec / WBTC 8 dec). In such pools `token0ScaleMultiplier ≥ 10^10`. An LP whose proportional scaled claim in a bin is below that threshold — which occurs naturally when many LPs share a bin or when the bin's token balance is small — will silently receive nothing on withdrawal. No privileged role, no malicious setup, and no non-standard token is required; the trigger is a normal `removeLiquidity` call by any LP.

---

### Recommendation

Add a revert guard immediately before the transfer block that fires whenever the scaled claim is non-zero but the native amount rounds to zero:

```solidity
if (totalToken0ToRemoveScaled > 0 && amount0Removed == 0) revert RemovalRoundsToZero();
if (totalToken1ToRemoveScaled > 0 && amount1Removed == 0) revert RemovalRoundsToZero();
```

Alternatively, mirror the `addLiquidity` asymmetry by using `Math.Rounding.Ceil` for the remove conversion as well — this slightly over-pays the LP (by at most 1 native unit) but preserves the invariant that a non-zero scaled claim always yields a non-zero native transfer. The pool's solvency buffer from the `addLiquidity` ceil-rounding absorbs this.

---

### Proof of Concept

**Setup:** USDC (token0, 6 dec) / WETH (token1, 18 dec) pool.  
`token0ScaleMultiplier = 10^12`, `minimalMintableLiquidity = 1000`.

1. **Alice** adds `1_000` shares to bin 4 (above current price, token0-only bin).  
   - Bin is empty → `amount0Scaled = ceil(initialScaledToken0PerShareE18 × 1000 / 1e18)`.  
   - Suppose `initialScaledToken0PerShareE18 = 10^12` → `amount0Scaled = 1000`, `amount0Added = 1` USDC.  
   - State: `binState.token0BalanceScaled = 1000`, `binTotalShares[4] = 1000`, Alice's shares = 1000.

2. **Bob** adds `10^9` shares to the same bin.  
   - `amount0Scaled = ceil(1000 × 10^9 / 1000) = 10^9`.  
   - State: `binState.token0BalanceScaled = 10^9 + 1000 ≈ 10^9`, `binTotalShares[4] = 10^9 + 1000`.

3. **Alice** calls `removeLiquidity` to burn all 1000 shares.  
   - `amount0Scaled = (10^9 + 1000) × 1000 / (10^9 + 1000) = 1000`.  
   - `amount0Removed = 1000 / 10^12 = 0` (floor).  
   - Alice's 1000 shares are erased; she receives **0 USDC**.  
   - Bob's effective claim on the bin increases by Alice's forfeited 1000 scaled units.

Alice has lost her entire 1 USDC deposit. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L141-142)
```text
      (amount0Added, amount1Added) =
        _deltasScaledToExternal(totalToken0ToAddScaled, totalToken1ToAddScaled, ctx, Math.Rounding.Ceil);
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L204-214)
```text
          BinState storage binState = binStates[binIdx];
          uint256 amount0Scaled = _checkedMul(binState.token0BalanceScaled, sharesToRemove) / binTotalSharesVal;
          uint256 amount1Scaled = _checkedMul(binState.token1BalanceScaled, sharesToRemove) / binTotalSharesVal;

          // casting to uint104 is safe because amount0Scaled and amount1Scaled are less than token(0|1)BalanceScaled
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token0BalanceScaled -= uint104(amount0Scaled);
          // forge-lint: disable-next-line(unsafe-typecast)
          binState.token1BalanceScaled -= uint104(amount1Scaled);
          binTotalShares[binIdx] = binTotalSharesVal - sharesToRemove;
          positionBinShares[posKey] = newUserShares;
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L239-247)
```text
      (amount0Removed, amount1Removed) =
        _deltasScaledToExternal(totalToken0ToRemoveScaled, totalToken1ToRemoveScaled, ctx, Math.Rounding.Floor);

      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L265-278)
```text
  function _deltasScaledToExternal(
    uint256 scaledDeltaAmount0,
    uint256 scaledDeltaAmount1,
    PoolContext memory ctx,
    Math.Rounding rounding
  ) internal pure returns (uint256 deltaAmount0, uint256 deltaAmount1) {
    if (rounding == Math.Rounding.Ceil) {
      deltaAmount0 = Math.ceilDiv(scaledDeltaAmount0, ctx.token0ScaleMultiplier);
      deltaAmount1 = Math.ceilDiv(scaledDeltaAmount1, ctx.token1ScaleMultiplier);
    } else {
      deltaAmount0 = scaledDeltaAmount0 / ctx.token0ScaleMultiplier;
      deltaAmount1 = scaledDeltaAmount1 / ctx.token1ScaleMultiplier;
    }
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L48-51)
```text
  /// @notice Multiplier to scale token0 external amounts to internal: 10^(max(18, decimals) - token0.decimals())
  uint256 internal immutable TOKEN_0_SCALE_MULTIPLIER;
  /// @notice Multiplier to scale token1 external amounts to internal: 10^(max(18, decimals) - token1.decimals())
  uint256 internal immutable TOKEN_1_SCALE_MULTIPLIER;
```
