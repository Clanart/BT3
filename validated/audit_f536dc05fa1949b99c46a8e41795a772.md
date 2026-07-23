Looking at the actual code in `OracleValueStopLossExtension.sol`, I need to verify each claim against the real implementation.

Audit Report

## Title
`OracleValueStopLossExtension` per-bin `BinHighWatermarks` not reset on full bin exit, causing permanent swap blocking after re-entry — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

## Summary
When all liquidity is removed from a bin (`totalShares == 0`), `_afterSwapOracleStopLoss` skips that bin via `continue` but leaves `highWatermarks[pool_][binIdx]` storage intact with elevated values from the prior liquidity era. When new LPs later re-enter the same bin, the first swap compares the new per-share metrics against the stale watermarks; if the gap exceeds the configured drawdown floor, `_checkAndUpdateWatermarks` reverts with `OracleStopLossTriggered`, blocking swaps in that direction. With `decayPerSecondE8 == 0` the block is permanent until a timelocked admin action manually resets the watermarks.

## Finding Description

In `_afterSwapOracleStopLoss`, the loop over touched bins explicitly skips empty bins:

```solidity
for (uint256 i = 0; i < count; i++) {
    uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
    if (totalShares == 0) continue;   // ← no delete of highWatermarks[pool_][binIdxs[i]]
    ...
    _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
}
``` [1](#0-0) 

The `continue` skips the watermark update but leaves `highWatermarks[pool_][binIdx]` (a `BinHighWatermarks` struct with `token0`, `token1`, `lastDecayTs`) untouched in storage, retaining whatever elevated values were written during the previous era of liquidity.

When new LPs add liquidity to the previously empty bin and a swap touches it, `totalShares > 0` so the `continue` is not taken. `_checkAndUpdateWatermarks` is called, which reads the stale storage:

```solidity
BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
uint256 dt = block.timestamp - hwmS.lastDecayTs;
(uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
if (breach0 && zeroForOne) {
    revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
}
``` [2](#0-1) 

`_applyWatermark` reports a breach when `metric < (hwm * floorMultiplier) / E6`. If the stale watermark is elevated from profitable trading in the prior era and the new per-share metric (computed from the fresh deposit) falls below the drawdown floor, the revert fires.

With `decayPerSecondE8 == 0` (explicitly documented as a valid setting at line 129), `_decayed` returns the watermark unchanged regardless of elapsed time:

```solidity
if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
``` [3](#0-2) 

The only recovery path is a timelocked admin call to `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks`, which requires admin awareness and waiting out the pool's timelock: [4](#0-3) 

No automatic or permissionless recovery exists.

## Impact Explanation
Swaps in the affected direction revert with `OracleStopLossTriggered` for the re-entered bin. LPs who deposited into that bin earn zero fees from swaps until admin intervention. With `decayPerSecondE8 == 0` the block is permanent. This constitutes broken core pool swap functionality causing loss of LP fee revenue and unusable swap flows, matching the allowed impact gate.

## Likelihood Explanation
- Full bin exit is a routine LP operation (position rebalancing, risk-off exit); no privileged actor required.
- Re-entry to the same bin by new or returning LPs is expected pool lifecycle behavior.
- `decayPerSecondE8 == 0` is explicitly documented as a valid configuration (line 129 comment: "0 disables decay"), making the permanent-block variant the highest-risk but not an edge case.
- With decay enabled, the block persists until the watermark decays below the floor, which at the documented reference rate of 58 E8/s (~5%/day) takes multiple days for a 50% drawdown threshold.
- No malicious actor is required; any LP exiting fully and any subsequent LP re-entering triggers the condition.

## Recommendation
In `_afterSwapOracleStopLoss`, delete the watermark storage when a bin is found to be empty, so that re-entry initializes watermarks fresh from the first post-re-entry swap metric:

```solidity
if (totalShares == 0) {
    delete highWatermarks[pool_][binIdxs[i]];   // reset stale era watermarks
    continue;
}
```

This mirrors the correct pattern: an empty bin has no per-share value to protect, so its watermarks should not carry over to a new liquidity era.

## Proof of Concept
1. Pool configured with `OracleValueStopLossExtension`, `drawdownE6 = 500_000` (50%), `decayPerSecondE8 = 0`.
2. LP adds liquidity to bin 0; profitable swaps elevate per-share balances. After several swaps, `highWatermarks[pool][0] = {token0: 1000, token1: 1000, lastDecayTs: T}`.
3. LP removes **all** shares from bin 0 → `totalShares = 0`. Subsequent swaps skip bin 0 via `continue`; watermarks remain at `(1000, 1000)`.
4. New LP adds liquidity to bin 0. The bin's per-share metrics compute to `metricT0 = 400`, `metricT1 = 400` based on the deposit and current oracle price.
5. First swap touching bin 0: `_applyWatermark(400, 1000, 500_000)` → floor = `1000 * 500_000 / 1_000_000 = 500`; `400 < 500` → `breach0 = true` → `OracleStopLossTriggered` revert.
6. Swaps in the affected direction are permanently blocked. New LPs earn zero fees. Admin must propose and wait out the timelock to manually reset watermarks.

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L156-177)
```text
  /// @notice Propose per-bin high watermarks; applied after the pool timelock via execute.
  function proposeOracleStopLossHighWatermarks(address pool_, int8 binIdx, uint104 newHwmToken0, uint104 newHwmToken1)
    external
    onlyPoolAdmin(pool_)
  {
    _requireInitialized(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    pendingHighWatermark[pool_] =
      PendingHighWatermarks({token0: newHwmToken0, token1: newHwmToken1, binIdx: binIdx, executeAfter: executeAfter});
    emit OracleStopLossHighWatermarkProposed(pool_, binIdx, newHwmToken0, newHwmToken1, executeAfter);
  }

  /// @notice Apply the pending watermarks. Also resets the decay clock for the bin.
  function executeOracleStopLossHighWatermarks(address pool_) external onlyPoolAdmin(pool_) {
    PendingHighWatermarks memory pending = pendingHighWatermark[pool_];
    if (pending.executeAfter == 0) revert OracleStopLossNoPendingHighWatermark(pool_);
    _requireElapsed(pending.executeAfter);
    highWatermarks[pool_][pending.binIdx] =
      BinHighWatermarks({token0: pending.token0, token1: pending.token1, lastDecayTs: uint32(block.timestamp)});
    delete pendingHighWatermark[pool_];
    emit OracleStopLossHighWatermarkUpdated(pool_, pending.binIdx, pending.token0, pending.token1);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L236-242)
```text
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L267-273)
```text
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L319-320)
```text
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
```
