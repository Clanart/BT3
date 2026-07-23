### Title
`addLiquidity` missing `whenNotPaused` allows LP deposits during emergency pause, breaking the emergency mechanism — (File: `metric-core/contracts/MetricOmmPool.sol`)

### Summary
The `addLiquidity` function in `MetricOmmPool` is missing the `whenNotPaused` modifier. When the pool admin or protocol pauses the pool during an emergency, swaps are correctly blocked, but LP deposits are not. LPs can add principal to a pool in an emergency state, exposing those funds to loss when the pool is unpaused.

### Finding Description
`MetricOmmPool` defines a single `pauseLevel` state variable with three values: `0 = active`, `1 = paused by admin`, `2 = paused by protocol`. The `_checkNotPaused` helper reverts when `pauseLevel != 0`, and the `whenNotPaused` modifier wraps it. [1](#0-0) [2](#0-1) 

`swap` correctly carries `whenNotPaused`: [3](#0-2) 

But `addLiquidity` does not: [4](#0-3) 

The factory's `pausePool` and `protocolPausePool` both set `pauseLevel` to `1` or `2` respectively, with the documented intent of halting pool activity: [5](#0-4) [6](#0-5) 

Because `addLiquidity` has no `whenNotPaused` guard, any caller can deposit token0 and token1 into the pool while `pauseLevel != 0`. The pool's `LiquidityLib.addLiquidity` path executes fully — updating `binTotals`, `_binStates`, `_binTotalShares`, and `_positionBinShares` — and the pool's `metricOmmModifyLiquidityCallback` pulls real tokens from the depositor.

### Impact Explanation
The pool is paused precisely because something is wrong (e.g., the oracle is returning bad prices, or a price anomaly was detected). During the pause, swaps are blocked so no further damage occurs. However, LPs who call `addLiquidity` during the pause transfer real tokens into the pool. When the admin later unpauses the pool, swaps resume. If the emergency condition (e.g., a stale or manipulated oracle price) persists or recurs, arbitrageurs can immediately drain the newly deposited LP principal at the bad price. The admin's emergency mechanism is broken: it cannot prevent new capital from entering the pool during a crisis.

This is the direct analog of the Cork M-2 bug: there, `LVDepositNotPaused` checked `isWithdrawalPaused` instead of `isDepositPaused`, so deposits were never actually paused. Here, the guard that should protect `addLiquidity` is simply absent, producing the same outcome — the admin cannot pause deposits alone.

### Likelihood Explanation
Medium. The trigger requires: (1) the admin pauses the pool due to an emergency, and (2) at least one LP calls `addLiquidity` during the pause window. Both are plausible in production: emergencies are the exact scenario the pause mechanism is designed for, and LPs monitoring on-chain state may not notice the pause before submitting a pending transaction.

### Recommendation
Add `whenNotPaused` to `addLiquidity`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY)
  returns (uint256 amount0Added, uint256 amount1Added)
```

`removeLiquidity` may intentionally remain unguarded to allow LPs to exit during a pause; that design choice does not affect this fix.

### Proof of Concept

1. Oracle begins returning a manipulated price for the pool's token pair.
2. Pool admin calls `MetricOmmPoolFactory.pausePool(pool)` → `pauseLevel = 1`. Swaps now revert with `PoolPaused`.
3. LP submits `addLiquidity(owner, salt, deltas, callbackData, extensionData)`. The call succeeds — no `whenNotPaused` check — and the pool pulls `amount0Added` of token0 and `amount1Added` of token1 from the LP.
4. Admin fixes the oracle and calls `unpausePool(pool)` → `pauseLevel = 0`.
5. Arbitrageur immediately calls `swap` at the (still-stale or briefly-bad) oracle price, extracting value from the bins that now include the LP's freshly deposited tokens.
6. LP's deposited principal is partially or fully drained; the LP receives far less than deposited upon `removeLiquidity`.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L174-177)
```text
  modifier whenNotPaused() {
    _checkNotPaused();
    _;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-196)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
    if (deltas.binIdxs.length == 0) return (0, 0);
    if (deltas.binIdxs.length != deltas.shares.length) revert LiquidityDeltaLengthMismatch();
    _beforeAddLiquidity(msg.sender, owner, salt, deltas, extensionData);
    (amount0Added, amount1Added) = LiquidityLib.addLiquidity(
      _liquidityContext(), owner, salt, deltas, callbackData, binTotals, _binStates, _binTotalShares, _positionBinShares
    );
    _afterAddLiquidity(msg.sender, owner, salt, deltas, amount0Added, amount1Added, extensionData);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L217-224)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
```

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L392-396)
```text
  function protocolPausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0 && cur != 1) revert InvalidPauseTransition(cur, 2);
    IMetricOmmPoolFactoryActions(pool).setPause(2);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L460-464)
```text
  function pausePool(address pool) external override nonReentrant onlyPoolAdmin(pool) {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0) revert InvalidPauseTransition(cur, 1);
    IMetricOmmPoolFactoryActions(pool).setPause(1);
  }
```
