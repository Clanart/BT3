The code is clear. Let me trace the exact arithmetic before rendering a verdict.

**`_afterTimelock` (line 297–299):**
```solidity
function _afterTimelock(address pool_) private view returns (uint32) {
    return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
}
```

The addition runs in `uint256` (Solidity 0.8 — no overflow revert). The `uint32(...)` explicit downcast **silently truncates** — it does not revert.

**`executeOracleStopLossTimelock` (line 86–94):**
```solidity
if (sched.pendingTimelockExecuteAfter == 0) revert OracleStopLossNoPendingTimelock(pool_);
_requireElapsed(sched.pendingTimelockExecuteAfter);
```

**`_requireElapsed` (line 301–303):**
```solidity
if (block.timestamp < executeAfter) revert OracleStopLossTimelockNotElapsed(...);
```

---

**Correcting the proof idea's exact-zero case first:**

The question claims wrapping to `0` lets `executeOracleStopLossTimelock` succeed. That is wrong — `pendingTimelockExecuteAfter == 0` causes an immediate **revert** (`OracleStopLossNoPendingTimelock`). The exact-zero wrap makes the proposal permanently unexecutable (a self-DoS on the admin), not a bypass.

**The real bypass — wrap to any small non-zero value:**

With `block.timestamp ≈ 1_753_000_000` (July 2026) and `type(uint32).max = 4_294_967_295`:

Set `timelock = type(uint32).max - block.timestamp + 2 = 2_541_967_297` (fits in `uint32`).

Then:
```
block.timestamp + timelock = 1_753_000_000 + 2_541_967_297 = 4_294_967_297
uint32(4_294_967_297) = 1   // truncated to lower 32 bits
```

`pendingTimelockExecuteAfter = 1` → passes the `!= 0` guard → `_requireElapsed(1)` checks `block.timestamp < 1` → false → **timelock bypassed immediately**.

**Attack path:**
1. Pool admin proposes `newTimelock = type(uint32).max - block.timestamp + 2` via `proposeOracleStopLossTimelock`.
2. Waits for the existing (legitimate) timelock to elapse, then calls `executeOracleStopLossTimelock` — now `oracleStopLossConfig[pool_].timelock` is the huge value.
3. Proposes any subsequent change (drawdown, decay, watermarks, or another timelock).
4. `_afterTimelock` returns `1`; `pendingXxxExecuteAfter = 1`.
5. Immediately calls the corresponding `executeOracleStopLoss*` — passes both guards, executes with zero wait.

**No `_validateTimelock` exists** — confirmed by grep returning no matches. `_validateDrawdown` and `_validateDecay` have caps; timelock has none.

**Scope alignment:** The allowed impacts explicitly include *"Admin-boundary break: pool admin exceeds caps, bypasses timelocks."* The pool admin is not listed as a trusted-reject role (only factory owner, oracle admin, deployer are). The timelock is the LP's only protection against pool admin parameter changes.

---

### Title
`_afterTimelock` uint32 truncation lets pool admin permanently bypass all timelocks — (`metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol`)

### Summary
`_afterTimelock` casts `block.timestamp + timelock` to `uint32` without bounding the input. A pool admin who sets the stored timelock to any value satisfying `block.timestamp + timelock ≡ k (mod 2^32)` for small `k > 0` causes every subsequent proposal's `pendingXxxExecuteAfter` to be set to `k`, which `_requireElapsed` passes immediately.

### Finding Description
`_afterTimelock` computes:

```solidity
return uint32(block.timestamp + oracleStopLossConfig[pool_].timelock);
``` [1](#0-0) 

The addition is performed in `uint256` (no revert), then silently truncated to 32 bits. There is no cap or validation on the `timelock` field at proposal time: [2](#0-1) 

`executeOracleStopLossTimelock` guards only against `== 0`: [3](#0-2) 

`_requireElapsed` checks `block.timestamp < executeAfter`, which is trivially false for any `executeAfter` in the past (e.g., `1`): [4](#0-3) 

The same `_afterTimelock` is called for drawdown, decay, and watermark proposals — all are affected: [5](#0-4) [6](#0-5) [7](#0-6) 

### Impact Explanation
Once the pool admin executes the large-timelock change (after waiting through the current legitimate timelock once), every future parameter change — drawdown reduction, decay increase, watermark reset — can be executed in the same block as the proposal. LPs lose the reaction window the timelock is designed to provide. The pool admin can immediately disable stop-loss protection (`drawdownE6 = 0`) or set watermarks to zero, removing all value-loss guards while LPs have no time to exit.

### Likelihood Explanation
Requires the pool admin to be malicious and to first wait through one legitimate timelock cycle to install the large timelock value. After that, the bypass is permanent and affects all future proposals. No unprivileged caller is needed; the pool admin role is the sole actor.

### Recommendation
Add a maximum cap on the timelock value in both `initialize` and `proposeOracleStopLossTimelock`, analogous to `_validateDrawdown` and `_validateDecay`. For example:

```solidity
uint32 private constant MAX_TIMELOCK = 365 days; // ~31.5M seconds, well below uint32 overflow

function _validateTimelock(uint32 timelock) private pure {
    if (timelock > MAX_TIMELOCK) revert OracleStopLossTimelockTooLarge(timelock);
}
```

Apply this in `initialize` and `proposeOracleStopLossTimelock` before storing the value.

### Proof of Concept
```solidity
// Foundry test sketch
uint32 ts = uint32(block.timestamp); // ~1_753_000_000
uint32 maliciousTimelock = type(uint32).max - ts + 2; // wraps sum to 1

// Step 1: pool admin proposes maliciousTimelock, waits existing timelock, executes
// Step 2: pool admin proposes drawdown change
//   _afterTimelock returns uint32(block.timestamp + maliciousTimelock) = 1
//   pendingDrawdownExecuteAfter = 1
// Step 3: pool admin immediately calls executeOracleStopLossDrawdown
//   pendingDrawdownExecuteAfter != 0  → passes
//   block.timestamp < 1              → false → passes
//   drawdown updated with zero wait
assertEq(uint32(uint256(ts) + uint256(maliciousTimelock)), 1);
```

### Citations

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L78-84)
```text
  function proposeOracleStopLossTimelock(address pool_, uint32 newTimelock) external onlyPoolAdmin(pool_) {
    PoolStopLossSchedule storage sched = _initializedSchedule(pool_);
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingTimelock = newTimelock;
    sched.pendingTimelockExecuteAfter = executeAfter;
    emit OracleStopLossTimelockProposed(pool_, newTimelock, executeAfter);
  }
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L88-89)
```text
    if (sched.pendingTimelockExecuteAfter == 0) revert OracleStopLossNoPendingTimelock(pool_);
    _requireElapsed(sched.pendingTimelockExecuteAfter);
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L106-108)
```text
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDrawdownE6 = uint32(newMaxDrawdownE6);
    sched.pendingDrawdownExecuteAfter = executeAfter;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L133-135)
```text
    uint32 executeAfter = _afterTimelock(pool_);
    sched.pendingDecayPerSecondE8 = uint32(newDecayPerSecondE8);
    sched.pendingDecayExecuteAfter = executeAfter;
```

**File:** metric-periphery/contracts/extensions/OracleValueStopLossExtension.sol (L162-164)
```text
    uint32 executeAfter = _afterTimelock(pool_);
    pendingHighWatermark[pool_] =
      PendingHighWatermarks({token0: newHwmToken0, token1: newHwmToken1, binIdx: binIdx, executeAfter: executeAfter});
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
