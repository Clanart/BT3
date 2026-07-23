Looking at the `OracleValueStopLossExtension`, I can identify a direct analog to the `navPerShareHighMark` bug: per-bin high watermarks are never cleared when a bin is fully exited, causing false stop-loss triggers after re-entry.

---

### Title
`OracleValueStopLossExtension` per-bin `BinHighWatermarks` not reset on full bin exit, causing permanent swap blocking after re-entry — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

When all liquidity is removed from a bin (`totalShares == 0`), `_afterSwapOracleStopLoss` skips that bin but does **not** clear its `highWatermarks[pool_][binIdx]` storage. When new LPs later re-enter the same bin at the pool's initial per-share values, the first swap compares the new (lower) per-share metrics against the stale (higher) watermarks from the previous era. If the gap exceeds the configured drawdown threshold, `_checkAndUpdateWatermarks` reverts with `OracleStopLossTriggered`, permanently blocking swaps in that direction until a timelocked admin action resets the watermarks.

---

### Finding Description

`_afterSwapOracleStopLoss` iterates over touched bins and explicitly skips empty ones: [1](#0-0) 

The `continue` skips the watermark update but leaves `highWatermarks[pool_][binIdx]` untouched. The storage slot retains whatever `token0`, `token1`, and `lastDecayTs` values were written during the previous era of liquidity.

When new LPs add liquidity to the previously empty bin, the bin's per-share token balances start at the pool's immutable initial values (`INITIAL_SCALED_TOKEN_0_PER_SHARE_E18`, `INITIAL_SCALED_TOKEN_1_PER_SHARE_E18`). The first swap after re-entry calls `_checkAndUpdateWatermarks`: [2](#0-1) 

`_applyWatermark` ratchets up on new highs and reports a breach when the current metric falls below `hwm * floorMultiplier / E6`. If the old watermark was elevated by profitable trading in the previous era and the new metric (at initial per-share values) is below the drawdown floor, the function reverts: [3](#0-2) 

With `decayPerSecondE8 == 0` (decay explicitly disabled, as documented): [4](#0-3) 

…the watermarks never decay and the block is permanent. Even with decay enabled, the watermarks persist until they decay past the floor, which can take days at typical rates (the comment cites `58 ~= 5%/day`).

The only recovery path is a timelocked admin call to `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks`: [5](#0-4) 

This requires admin awareness of the problem and waiting out the pool's timelock — it is not automatic.

---

### Impact Explanation

Swaps in the affected direction are blocked for the re-entered bin. LPs who deposited into that bin earn zero fees from swaps until admin intervention. With `decayPerSecondE8 == 0` the block is permanent. This is broken core pool swap functionality causing loss of LP fee revenue and unusable swap flows, matching the allowed impact gate.

---

### Likelihood Explanation

- Pools using `OracleValueStopLossExtension` with `decayPerSecondE8 == 0` are the highest-risk configuration; the extension's own docs describe `0` as a valid setting.
- Full bin exit is a normal LP operation (position rebalancing, risk-off exit).
- Re-entry to the same bin by new LPs is expected pool lifecycle behavior.
- The initial per-share values are fixed at pool creation and are typically lower than values reached after profitable trading, making the metric-vs-watermark gap likely.
- No privileged attacker is required; any LP exiting and any subsequent LP re-entering triggers the condition.

---

### Recommendation

In `_afterSwapOracleStopLoss`, delete the watermark storage when a bin is found to be empty, mirroring the `navPerShareHighMark` fix pattern:

```solidity
if (totalShares == 0) {
    delete highWatermarks[pool_][binIdxs[i]];   // reset stale era watermarks
    continue;
}
```

This ensures that when new LPs re-enter a previously empty bin, the watermarks are initialized fresh from the first post-re-entry swap metric rather than compared against a stale high from a prior era.

---

### Proof of Concept

1. Pool configured with `OracleValueStopLossExtension`, `drawdownE6 = 500_000` (50%), `decayPerSecondE8 = 0`.
2. LP adds liquidity to bin 0; profitable swaps raise per-share balances. After several swaps, `hwm0 = 1000`, `hwm1 = 1000` are written to `highWatermarks[pool][0]`.
3. LP removes **all** shares from bin 0 → `totalShares = 0`. Subsequent swaps skip bin 0 via `continue`; watermarks remain at `(1000, 1000)`.
4. New LP adds liquidity to bin 0 at the pool's initial per-share values. The bin's per-share metrics compute to `metricT0 = 400`, `metricT1 = 400`.
5. First swap touching bin 0: `_applyWatermark(400, 1000, 500_000)` → threshold = `1000 * 500_000 / 1_000_000 = 500`; `400 < 500` → `breach = true` → `OracleStopLossTriggered` revert.
6. Swaps in the affected direction are permanently blocked. New LPs earn zero fees. Admin must wait out the timelock to manually reset watermarks.

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

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L258-285)
```text
  function _checkAndUpdateWatermarks(
    address pool_,
    int8 binIdx,
    uint256 metricT0,
    uint256 metricT1,
    uint256 floorMultiplier,
    uint256 decayRate,
    bool zeroForOne
  ) private {
    BinHighWatermarks storage hwmS = highWatermarks[pool_][binIdx];
    uint256 dt = block.timestamp - hwmS.lastDecayTs;

    (uint256 hwm0, bool breach0) = _applyWatermark(metricT0, _decayed(hwmS.token0, decayRate, dt), floorMultiplier);
    if (breach0 && zeroForOne) {
      revert OracleStopLossTriggered(binIdx, true, metricT0, (hwm0 * floorMultiplier) / E6);
    }

    (uint256 hwm1, bool breach1) = _applyWatermark(metricT1, _decayed(hwmS.token1, decayRate, dt), floorMultiplier);
    if (breach1 && !zeroForOne) {
      revert OracleStopLossTriggered(binIdx, false, metricT1, (hwm1 * floorMultiplier) / E6);
    }

    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token0 = uint104(hwm0);
    // forge-lint: disable-next-line(unsafe-typecast)
    hwmS.token1 = uint104(hwm1);
    hwmS.lastDecayTs = uint32(block.timestamp);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L318-324)
```text
  /// @dev Linear decay; floors at 0 (ratchet restores from the live metric on next touch).
  function _decayed(uint256 hwm, uint256 ratePerSecondE8, uint256 dt) private pure returns (uint256) {
    if (ratePerSecondE8 == 0 || dt == 0 || hwm == 0) return hwm;
    uint256 factor = ratePerSecondE8 * dt;
    if (factor >= E8) return 0;
    return hwm - (hwm * factor) / E8;
  }
```
