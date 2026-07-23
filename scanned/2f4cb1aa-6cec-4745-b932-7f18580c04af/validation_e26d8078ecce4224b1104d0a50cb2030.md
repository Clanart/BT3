Looking at the `_scaleWeightsToShares` function in `MetricOmmPoolLiquidityAdder.sol`, there is a direct analog to the Balancer zero-balance free-share bug.

---

### Title
Unbounded Share Inflation via Zero-Need Probe in `addLiquidityWeighted` — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

### Summary

`addLiquidityWeighted` uses a two-phase probe/pay pattern. The probe call determines how many tokens (`need0`, `need1`) the pool requires for the weight-based shares. `_scaleWeightsToShares` then computes a scaling factor to fit within the user's `maxAmountToken0`/`maxAmountToken1` caps. When both `need0 == 0` and `need1 == 0` — which occurs when the targeted bins are empty — the scaling factor is set to `type(uint256).max`, inflating the actual shares to an astronomically large value. The subsequent paying `addLiquidity` call still requests 0 tokens from the pool (bins remain empty), so the attacker receives a massive share position for zero token cost.

### Finding Description

The vulnerable code is in `_scaleWeightsToShares`:

```solidity
uint256 scaleWad0 = need0 == 0 ? type(uint256).max : Math.mulDiv(max0, WAD, need0);
uint256 scaleWad1 = need1 == 0 ? type(uint256).max : Math.mulDiv(max1, WAD, need1);
uint256 scaleWad = scaleWad0 < scaleWad1 ? scaleWad0 : scaleWad1;
``` [1](#0-0) 

The design intent is: if one token is not needed, that leg is unconstrained and the other token's ratio governs scaling. However, when **both** legs are unconstrained (`need0 == 0 && need1 == 0`), `scaleWad` becomes `type(uint256).max` with no guard or revert. The per-bin share computation then becomes:

```solidity
out.shares[i] = Math.mulDiv(w.shares[i], scaleWad, WAD);
// e.g. Math.mulDiv(1e18, type(uint256).max, 1e18) == type(uint256).max
``` [2](#0-1) 

These inflated shares are passed to `_addLiquidity`, which calls `pool.addLiquidity`. The pool's callback fires with `amount0Delta = 0, amount1Delta = 0` (empty bins still need 0 tokens). The callback's cap check:

```solidity
if (amount0Delta > max0 || amount1Delta > max1) revert MaxAmountExceeded(...);
``` [3](#0-2) 

passes trivially (0 ≤ any cap), no tokens are transferred, and the attacker receives `type(uint256).max`-scale LP shares for free.

The probe path that triggers this:

```solidity
try IMetricOmmPoolActions(pool)
    .addLiquidity(owner, salt, weightDeltas, abi.encode(KIND_PROBE), extensionData) ...
} catch (bytes memory reason) {
    (uint256 need0, uint256 need1) = _decodeLiquidityProbeOrBubble(reason);
    LiquidityDelta memory scaled = _scaleWeightsToShares(weightDeltas, maxAmountToken0, maxAmountToken1, need0, need1);
    return _addLiquidity(pool, owner, salt, scaled, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
}
``` [4](#0-3) 

### Impact Explanation

An attacker who obtains free LP shares at `type(uint256).max` scale can:
1. Immediately redeem them against any subsequently added liquidity, draining both token0 and token1 from the pool.
2. Repeat across any pool whose targeted bins are empty, causing protocol-wide LP insolvency.

This is a direct loss of user principal (LP assets) and constitutes pool insolvency — LP share claims exceed actual pool balances.

### Likelihood Explanation

Empty bins are a normal pool state: a new pool before first deposit, bins outside the current price range after large price moves, or bins from which all liquidity was previously removed. An attacker can deliberately drain a specific bin (via swap or remove-liquidity) and then immediately call `addLiquidityWeighted` targeting that bin. No privileged access is required; `addLiquidityWeighted` is a public, payable function. [5](#0-4) 

### Recommendation

**Short term**: In `_scaleWeightsToShares`, revert explicitly when both needs are zero:

```solidity
if (need0 == 0 && need1 == 0) revert ZeroNeedProbe();
```

**Long term**: Mirror the Balancer recommendation — add an invariant check that the token amounts returned by the paying `addLiquidity` call are non-zero when non-zero shares are minted. Use fuzz testing (Echidna/Foundry invariant tests) to assert that `shares_minted > 0 → (amount0 > 0 || amount1 > 0)` across all bin states.

### Proof of Concept

1. Deploy or identify a pool where bin `B` has zero token0 and zero token1 balance (e.g., a freshly initialized pool or one where all liquidity in bin `B` was removed).
2. Call `addLiquidityWeighted(pool, owner, salt, weightDeltas=[{binIdx: B, shares: 1e18}], maxAmount0=1e18, maxAmount1=1e18, ...)`.
3. The probe fires → pool callback returns `amount0Delta=0, amount1Delta=0` → `LiquidityProbe(0, 0)` is caught.
4. `_scaleWeightsToShares` computes `scaleWad = type(uint256).max`; `out.shares[0] = type(uint256).max`.
5. `_addLiquidity` is called with `shares = type(uint256).max` for bin `B`.
6. Pool callback fires again with `amount0Delta=0, amount1Delta=0`; cap check passes; no tokens transferred.
7. Attacker holds `type(uint256).max` LP shares in bin `B` at zero cost.
8. A legitimate LP adds liquidity to bin `B` (e.g., 10,000 USDC + 10,000 DAI). Attacker immediately redeems their dominant share position, withdrawing the full deposit. Net loss to the legitimate LP equals their full deposit.

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L165-167)
```text
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L231-233)
```text
    uint256 scaleWad0 = need0 == 0 ? type(uint256).max : Math.mulDiv(max0, WAD, need0);
    uint256 scaleWad1 = need1 == 0 ? type(uint256).max : Math.mulDiv(max1, WAD, need1);
    uint256 scaleWad = scaleWad0 < scaleWad1 ? scaleWad0 : scaleWad1;
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L240-241)
```text
      out.shares[i] = Math.mulDiv(w.shares[i], scaleWad, WAD);
      if (w.shares[i] != 0 && out.shares[i] == 0) revert SharesRoundedToZero();
```
