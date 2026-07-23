### Title
`OracleValueStopLossExtension` LP-protection timelock can be zero, allowing admin to instantly disable stop-loss and drain LP principal — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

---

### Summary

`OracleValueStopLossExtension` is explicitly designed so that drawdown and decay changes are **timelocked** to give LPs time to exit before the protection parameters are weakened. However, the `timelock` field accepts `0` at initialization with no minimum enforcement, and the `_requireElapsed` check passes immediately when `executeAfter == block.timestamp`. A pool admin who initializes (or later reduces) the timelock to `0` can propose and execute a `drawdownE6 = 1_000_000` (100 % — stop-loss fully disabled) change in a single block, giving LPs zero seconds to react before their principal is exposed to extraction.

---

### Finding Description

`initialize` decodes three fields from `data` and stores them without validating `timelock`:

```solidity
(uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
_validateDrawdown(drawdownE6);   // ✓ validated
_validateDecay(decayPerSecondE8); // ✓ validated
// timelock — NO validation, 0 is silently accepted
oracleStopLossConfig[pool] = PoolStopLossConfig({
    drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
});
``` [1](#0-0) 

`_afterTimelock` computes `block.timestamp + timelock`. When `timelock == 0` this equals `block.timestamp`:

```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
``` [2](#0-1) 

`_requireElapsed` uses a strict-less-than check, so `block.timestamp < block.timestamp` is `false` — the guard passes immediately in the same block:

```solidity
function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
}
``` [3](#0-2) 

The protocol's own test suite confirms this is the intended behaviour for `timelock == 0`:

```solidity
function test_decayTimelockZeroExecutesImmediately() public {
    vm.startPrank(admin);
    extension.proposeOracleStopLossDecay(address(mockPool), 58);
    extension.executeOracleStopLossDecay(address(mockPool));   // same block, no warp
    vm.stopPrank();
    assertEq(_decay(), 58);
}
``` [4](#0-3) 

The same zero-timelock path applies to `proposeOracleStopLossDrawdown` / `executeOracleStopLossDrawdown` and `proposeOracleStopLossHighWatermarks` / `executeOracleStopLossHighWatermarks`. [5](#0-4) 

Additionally, the timelock can be **reduced to 0 post-deployment**: `proposeOracleStopLossTimelock(pool, 0)` is gated only by the *current* timelock. Once that wait elapses and the reduction executes, all subsequent parameter changes are instant. [6](#0-5) 

---

### Impact Explanation

The NatSpec of `OracleValueStopLossExtension` states:

> "Drawdown and decay changes are timelocked so LPs can react; monitor at least as often as the timelock or trust the pool admin." [7](#0-6) 

When `timelock == 0` this guarantee is void. The admin can atomically set `drawdownE6 = 1_000_000` (100 % — the maximum accepted by `_validateDrawdown`), which sets `floorMultiplier = E6 - 1_000_000 = 0`. With `floorMultiplier == 0`, the breach condition `metric < (hwm * 0) / E6 = 0` is never true, so `OracleStopLossTriggered` is never emitted and all swaps proceed unchecked. The admin (or a colluding MEV searcher) can then drain LP principal through adversarial swaps with no stop-loss resistance. This is a direct loss of LP-deposited principal. [8](#0-7) 

---

### Likelihood Explanation

**Medium-Low.** Requires a malicious or compromised pool admin. However, the stop-loss extension exists precisely to bound admin power over LP funds; a zero timelock completely removes that bound. LPs who deposit into a pool advertising stop-loss protection have no on-chain guarantee the timelock is non-zero. The reduction path (propose → wait current timelock → execute to 0 → instant disable) is reachable without any off-chain coordination beyond the admin's own transactions.

---

### Recommendation

1. Add a minimum timelock floor in `initialize` (e.g., `MIN_TIMELOCK = 1 days`) and revert if `timelock < MIN_TIMELOCK`.
2. Add the same floor check in `executeOracleStopLossTimelock` so the timelock can never be reduced below the minimum.
3. Consider enforcing `newTimelock >= currentTimelock` or at least `newTimelock >= MIN_TIMELOCK` in `proposeOracleStopLossTimelock`.

```solidity
uint32 private constant MIN_TIMELOCK = 1 days;

// in initialize:
if (timelock < MIN_TIMELOCK) revert OracleStopLossTimelockTooShort(timelock);

// in executeOracleStopLossTimelock:
if (timelock < MIN_TIMELOCK) revert OracleStopLossTimelockTooShort(timelock);
```

---

### Proof of Concept

```solidity
// Pool created with timelock = 0 in OracleValueStopLossExtension init data
// abi.encode(uint32(50_000), uint32(58), uint32(0))
//                drawdown      decay      timelock ← zero

// Admin atomically in one transaction:
extension.proposeOracleStopLossDrawdown(pool, 1_000_000); // 100% drawdown
extension.executeOracleStopLossDrawdown(pool);             // passes immediately

// Now floorMultiplier = 1e6 - 1e6 = 0
// _applyWatermark: breached = metric < (hwm * 0) / 1e6 = 0 → always false
// Stop-loss never triggers; admin drains pool via swaps with no revert.
``` [9](#0-8) [10](#0-9)

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L13-16)
```text
/// @title OracleValueStopLossExtension
/// @notice Tracks per-bin value per share in token0 and token1 terms at the oracle mid,
///         against decaying high watermarks. Drawdown and decay changes are timelocked so LPs
///         can react; monitor at least as often as the timelock or trust the pool admin.
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L56-62)
```text
    (uint32 drawdownE6, uint32 decayPerSecondE8, uint32 timelock) = abi.decode(data, (uint32, uint32, uint32));
    _validateDrawdown(drawdownE6);
    _validateDecay(decayPerSecondE8);

    oracleStopLossConfig[pool] = PoolStopLossConfig({
      drawdownE6: drawdownE6, decayPerSecondE8: decayPerSecondE8, timelock: timelock, initialized: true
    });
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L78-93)
```text
  function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    emit OracleStopLossTimelockProposed(pool_, newTimelock, executeAfter);
  }

  function executeOracleStopLossTimelock(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingTimelockExecuteAfter == 0) revert OracleStopLossNoPendingTimelock(pool_);
    _requireElapsed(sched.pendingTimelockExecuteAfter);
    uint32 timelock = sched.pendingTimelock;
    oracleStopLossConfig[pool_].timelock = timelock;
    (sched.pendingTimelock, sched.pendingTimelockExecuteAfter) = (0, 0);
    emit OracleStopLossTimelockSet(pool_, timelock);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L103-120)
```text
  function proposeOracleStopLossDrawdown(address pool_, uint256 newMaxDrawdownE6) external onlyPoolAdmin(pool_) {
    _validateDrawdown(newMaxDrawdownE6);
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
    emit OracleStopLossDrawdownProposed(pool_, newMaxDrawdownE6, executeAfter);
  }

  function executeOracleStopLossDrawdown(address pool_) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    if (sched.pendingDrawdownExecuteAfter == 0) revert OracleStopLossNoPendingDrawdown(pool_);
    _requireElapsed(sched.pendingDrawdownExecuteAfter);
    uint32 drawdown = sched.pendingDrawdownE6;
    oracleStopLossConfig[pool_].drawdownE6 = drawdown;
    (sched.pendingDrawdownE6, sched.pendingDrawdownExecuteAfter) = (0, 0);
    emit OracleStopLossDrawdownSet(pool_, drawdown);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L234-242)
```text
    uint256 floorMultiplier = E6 - drawdown;
    uint256 decayRate = cfg.decayPerSecondE8;
    for (uint256 i = 0; i < count; i++) {
      uint256 totalShares = PoolStateLibrary._decodeBinTotalShares(shares[i]);
      if (totalShares == 0) continue;
      (uint104 t0, uint104 t1,,,) = PoolStateLibrary._decodeBinState(states[i]);
      (uint256 metricT0, uint256 metricT1) = _metrics(t0, t1, totalShares, minShares, midPriceX64);
      _checkAndUpdateWatermarks(pool_, binIdxs[i], metricT0, metricT1, floorMultiplier, decayRate, zeroForOne);
    }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L297-299)
```text
  function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L301-303)
```text
  function _requireElapsed(uint32 executeAfter) private view {
    if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(executeAfter, block.timestamp);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L305-307)
```text
  function _validateDrawdown(uint256 drawdownE6) private pure {
    if (drawdownE6 > E6) revert OracleStopLossDrawdownTooLarge(drawdownE6);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L326-336)
```text
  /// @dev Ratchet up on new highs; report breach below the drawdown floor. Direction-aware
  ///      blocking is decided by the caller.
  function _applyWatermark(uint256 metric, uint256 hwm, uint256 floorMultiplier)
    private
    pure
    returns (uint256 newHwm, bool breached)
  {
    if (metric >= hwm) return (metric, false);
    breached = metric < (hwm * floorMultiplier) / E6;
    return (hwm, breached);
  }
```

**File:** metric-periphery/test/extensions/OracleValueStopLossSubExtension.t.sol (L249-255)
```text
  function test_decayTimelockZeroExecutesImmediately() public {
    vm.startPrank(admin);
    extension.proposeOracleStopLossDecay(address(mockPool), 58);
    extension.executeOracleStopLossDecay(address(mockPool));
    vm.stopPrank();
    assertEq(_decay(), 58);
  }
```
