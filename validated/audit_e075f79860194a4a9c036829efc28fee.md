Audit Report

## Title
`addLiquidity` missing `whenNotPaused` allows LP deposits during emergency pause — (File: `metric-core/contracts/MetricOmmPool.sol`)

## Summary
`MetricOmmPool.addLiquidity` lacks the `whenNotPaused` modifier that `swap` carries, so any LP can deposit real tokens into the pool while `pauseLevel != 0`. The pause mechanism is designed to halt all pool activity during emergencies, but it only blocks swaps, leaving the deposit path fully open. Tokens deposited during a pause are immediately at risk when the pool is unpaused.

## Finding Description
`MetricOmmPool` defines `pauseLevel` with three values (`0 = active`, `1 = admin-paused`, `2 = protocol-paused`). The `_checkNotPaused` helper reverts when `pauseLevel != 0`, and the `whenNotPaused` modifier wraps it. [1](#0-0) [2](#0-1) 

`swap` correctly carries `whenNotPaused`: [3](#0-2) 

`addLiquidity` does not — it only carries `nonReentrant`: [4](#0-3) 

The `_beforeAddLiquidity` call inside `addLiquidity` dispatches to configured extension hooks (e.g., `DepositAllowlistExtension`), not to any pause check — it is not a substitute guard: [5](#0-4) 

`pausePool` (pool admin) and `protocolPausePool` (factory owner) both set `pauseLevel` to `1` or `2` respectively: [6](#0-5) [7](#0-6) 

With `pauseLevel != 0`, `swap` reverts but `addLiquidity` executes fully — updating `binTotals`, `_binStates`, `_binTotalShares`, and `_positionBinShares` — and the pool's `metricOmmModifyLiquidityCallback` pulls real tokens from the depositor.

## Impact Explanation
The pause mechanism exists to halt pool activity during emergencies (e.g., stale or manipulated oracle prices). Swaps are blocked, but LP deposits are not. Tokens deposited during the pause window are committed to the pool's bin state. When the admin unpauses, swaps resume immediately. If the emergency condition (bad oracle price) persists or recurs, arbitrageurs can drain the newly deposited LP principal at the bad price. This constitutes a direct loss of LP principal — a Medium/High impact matching the "direct loss of user principal" and "broken core pool functionality causing loss of funds" allowed impact categories.

## Likelihood Explanation
Medium. Two conditions are required: (1) the admin pauses the pool due to an emergency, and (2) at least one LP calls `addLiquidity` during the pause window. Both are plausible in production — emergencies are the exact scenario the pause mechanism targets, and LPs with pending transactions may not observe the pause before their transaction lands. The exploit requires no special privileges; any LP address can trigger it.

## Recommendation
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

## Proof of Concept

1. Oracle begins returning a manipulated price for the pool's token pair.
2. Pool admin calls `MetricOmmPoolFactory.pausePool(pool)` → `pauseLevel = 1`. Swaps now revert with `PoolPaused`.
3. LP calls `addLiquidity(owner, salt, deltas, callbackData, extensionData)`. The call succeeds — no `whenNotPaused` check — and the pool pulls `amount0Added` of token0 and `amount1Added` of token1 from the LP, updating `binTotals`, `_binStates`, `_binTotalShares`, and `_positionBinShares`.
4. Admin fixes the oracle and calls `unpausePool(pool)` → `pauseLevel = 0`.
5. Arbitrageur immediately calls `swap` at the (still-stale or briefly-bad) oracle price, extracting value from the bins that now include the LP's freshly deposited tokens.
6. LP's deposited principal is partially or fully drained; the LP receives far less than deposited upon `removeLiquidity`.

A Foundry test can reproduce this by: deploying a pool, calling `pausePool`, asserting `swap` reverts with `PoolPaused`, then asserting `addLiquidity` succeeds and tokens are transferred, confirming the missing guard.

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

**File:** metric-core/contracts/ExtensionCalling.sol (L88-99)
```text
  function _beforeAddLiquidity(
    address sender,
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_ADD_LIQUIDITY_ORDER,
      abi.encodeCall(IMetricOmmExtensions.beforeAddLiquidity, (sender, owner, salt, deltas, extensionData))
    );
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
