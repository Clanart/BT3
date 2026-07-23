Audit Report

## Title
`addLiquidity` Callable on Paused Pool, Exposing LP Deposits to Compromised State — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary

`MetricOmmPool.addLiquidity` lacks the `whenNotPaused` modifier that `swap` correctly applies. When a pool is paused at level 1 or 2 due to an emergency, any caller can still invoke `addLiquidity`, causing real tokens to be pulled from the LP's wallet and committed into the compromised pool via `metricOmmModifyLiquidityCallback`. There is no rollback path once the deposit is committed.

## Finding Description

The `whenNotPaused` modifier delegates to `_checkNotPaused()`, which reverts if `pauseLevel != 0`: [1](#0-0) [2](#0-1) 

`swap` is correctly guarded: [3](#0-2) 

`addLiquidity` is not: [4](#0-3) 

`_beforeAddLiquidity` only dispatches to optional extensions and contains no pause check: [5](#0-4) 

The periphery `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` and `addLiquidityWeighted` perform no pause state check before calling `pool.addLiquidity`: [6](#0-5) [7](#0-6) 

Once `pool.addLiquidity` proceeds, `metricOmmModifyLiquidityCallback` fires and immediately pulls real tokens from the LP into the paused pool: [8](#0-7) 

The factory confirms pausing is an emergency action: [9](#0-8) 

## Impact Explanation

An LP who submits `addLiquidityExactShares` or `addLiquidityWeighted` while the pool is paused (pauseLevel 1 or 2) will have their `token0`/`token1` pulled from their wallet and fully committed into a pool whose swap path is known to be unsafe. The bin accounting and `binTotals` are updated atomically with no rollback, resulting in direct loss of LP principal. This is a broken core pool functionality causing direct loss of user funds, meeting the Critical/High threshold.

## Likelihood Explanation

No privileged access is required. Any LP with a pre-approved `MetricOmmPoolLiquidityAdder` allowance who submits a pending deposit transaction — from a bot, queued multicall, or manual submission — during the window between the pause transaction and resolution of the underlying issue will have their funds deposited into the compromised pool. The LP is the victim of their own valid transaction executing against a paused pool with no on-chain protection.

## Recommendation

Add the `whenNotPaused` modifier to `addLiquidity` in `MetricOmmPool.sol`:

```solidity
function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

`removeLiquidity` should intentionally remain unguarded so LPs can always exit a paused pool.

## Proof of Concept

1. Pool deployed with `pauseLevel = 0` (active).
2. Vulnerability discovered; admin calls `factory.pausePool(pool)` → `pauseLevel = 1`.
3. `pool.swap(...)` reverts with `PoolPaused`.
4. LP calls `liquidityAdder.addLiquidityExactShares(pool, owner, salt, deltas, max0, max1, "")`.
5. `_addLiquidity` calls `pool.addLiquidity(...)` — no pause check — which succeeds.
6. `metricOmmModifyLiquidityCallback` fires; `pay(token0, payer, pool, amount0Delta)` and `pay(token1, payer, pool, amount1Delta)` pull real tokens from the LP into the paused pool.
7. LP's tokens are locked in a pool whose swap path is known to be unsafe, with no mechanism to prevent or reverse the deposit.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L174-177)
```text
  modifier whenNotPaused() {
    _checkNotPaused();
    _;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-188)
```text
  function addLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L56-68)
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
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-178)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }

    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
    _clearPayContext();
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L392-396)
```text
  function protocolPausePool(address pool) external override nonReentrant onlyOwner {
    (uint8 cur,,,,,) = PoolStateLibrary._slot0(pool);
    if (cur != 0 && cur != 1) revert InvalidPauseTransition(cur, 2);
    IMetricOmmPoolFactoryActions(pool).setPause(2);
  }
```
