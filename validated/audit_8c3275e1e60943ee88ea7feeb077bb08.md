Based on my analysis of the codebase, I found a direct analog.

---

### Title
`removeLiquidity` (and `addLiquidity`) bypass the pool pause mechanism, allowing LP fund outflow during emergency freezes — (`metric-core/contracts/MetricOmmPool.sol`)

### Summary
`MetricOmmPool` has a `pauseLevel` state variable and a `whenNotPaused` modifier that is applied to `swap()`, but neither `addLiquidity()` nor `removeLiquidity()` carry this modifier. When the pool is paused to stop operations during an exploit, oracle failure, or legal requirement, LPs can still freely withdraw their principal by calling `removeLiquidity` directly.

### Finding Description
The pool defines a two-level pause system:

```solidity
// line 72
uint8 internal pauseLevel;
// 0 = active, 1 = paused by admin, 2 = paused by protocol
``` [1](#0-0) 

The `_checkNotPaused` guard and `whenNotPaused` modifier exist and are applied to `swap()`:

```solidity
function swap(...) external whenNotPaused nonReentrant(PoolActions.SWAP) ...
``` [2](#0-1) [3](#0-2) 

However, `removeLiquidity` carries only `nonReentrant` — no pause check:

```solidity
function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    nonReentrant(PoolActions.REMOVE_LIQUIDITY)   // ← no whenNotPaused
    returns (uint256 amount0Removed, uint256 amount1Removed)
``` [4](#0-3) 

`addLiquidity` has the same omission: [5](#0-4) 

### Impact Explanation
When the factory owner or protocol sets `pauseLevel > 0` to freeze the pool (e.g., during an oracle manipulation event, an active exploit, or a regulatory freeze), swaps are correctly blocked. However, any LP can still call `removeLiquidity` directly on the pool to withdraw their token0/token1 share. This creates uncontrolled fund outflow that the pause mechanism was designed to prevent. In an insolvency or exploit scenario, LPs who act first drain the pool before the situation is resolved, leaving later LPs with nothing — a classic bank-run enabled by the incomplete pause surface.

### Likelihood Explanation
The trigger is unprivileged: any LP who holds position shares can call `removeLiquidity` at any time, including while `pauseLevel == 1` or `pauseLevel == 2`. No special role or setup is required beyond having previously added liquidity. The factory's `setPause` path is the only mitigation, and it demonstrably does not cover this function. [6](#0-5) 

### Recommendation
Add `whenNotPaused` to both `removeLiquidity` and `addLiquidity` in `MetricOmmPool`, consistent with how `swap` is already guarded. If the protocol intentionally wants to allow liquidity removal during a pause (e.g., to let LPs exit), that policy decision should be explicit and documented, and a separate, finer-grained pause flag should be introduced for each operation class.

### Proof of Concept
1. Factory owner calls `MetricOmmPoolFactory.setPause(pool, 1)` to pause the pool due to an ongoing oracle exploit.
2. `swap()` correctly reverts with `PoolPaused()` for all callers.
3. Attacker (an LP) calls `pool.removeLiquidity(owner, salt, deltas, "")` directly.
4. The call succeeds — `nonReentrant` passes, no pause check fires, `LiquidityLib.removeLiquidity` executes, and token0/token1 are transferred out of the pool to the attacker.
5. The protocol's emergency freeze is bypassed; LP principal drains while the pool is nominally "paused." [4](#0-3)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L72-72)
```text
  uint8 internal pauseLevel;
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

**File:** metric-core/contracts/MetricOmmPool.sol (L455-461)
```text
  function setPause(uint8 newLevel) external onlyFactory {
    if (newLevel > 2) revert InvalidPauseLevel();
    if (newLevel == pauseLevel) return;
    uint8 prev = pauseLevel;
    pauseLevel = newLevel;
    emit PauseLevelUpdated(prev, newLevel);
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L643-645)
```text
  function _checkNotPaused() internal view {
    if (pauseLevel != 0) revert PoolPaused();
  }
```
