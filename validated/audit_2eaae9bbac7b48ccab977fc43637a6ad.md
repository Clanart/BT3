Audit Report

## Title
`addLiquidity` bypasses `whenNotPaused` guard, allowing token deposits into a paused pool — (File: metric-core/contracts/MetricOmmPool.sol)

## Summary
`MetricOmmPool` exposes a `whenNotPaused` modifier backed by `_checkNotPaused()` that reverts when `pauseLevel != 0`. `swap` is correctly gated by this modifier, but `addLiquidity` carries only `nonReentrant`, leaving the pause mechanism entirely ineffective for liquidity deposits. Any caller — including users routing through `MetricOmmPoolLiquidityAdder` — can deposit real tokens into a paused, potentially corrupted pool.

## Finding Description
The `whenNotPaused` modifier is defined at [1](#0-0)  and calls `_checkNotPaused()` which reverts when `pauseLevel != 0` [2](#0-1) .

`swap` correctly applies this guard: [3](#0-2) 

`addLiquidity` does not — it carries only `nonReentrant(PoolActions.ADD_LIQUIDITY)` with no pause check: [4](#0-3) 

The exploit path through the periphery is fully reachable: `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` calls `_addLiquidity`, which calls `pool.addLiquidity` [5](#0-4) , which in turn issues a `metricOmmModifyLiquidityCallback` that pulls tokens from the payer via `pay()` [6](#0-5) . No step in this path checks `pauseLevel`.

## Impact Explanation
When the pool is paused due to an accounting discrepancy, new shares are minted against potentially corrupted `binTotals` / `_binStates`. The depositor receives inflated or deflated shares relative to the tokens transferred in. Upon `removeLiquidity` after the pool is unpaused (possibly with a haircut applied to bin balances during remediation), the depositor recovers a different amount than deposited — direct loss of principal. This meets the "direct loss of user principal" threshold under the allowed impact gate.

## Likelihood Explanation
Medium. The pool must first be paused by an admin or protocol action, but once paused the bypass is unconditional and requires no special privilege. Any EOA or contract can call `addLiquidity` directly or via `MetricOmmPoolLiquidityAdder`. Users interacting through aggregators or the liquidity adder will trigger the bypass inadvertently without any awareness that the pool is paused.

## Recommendation
Add `whenNotPaused` to `addLiquidity`, mirroring the guard already present on `swap`:

```solidity
function addLiquidity(
  address owner,
  uint80 salt,
  LiquidityDelta calldata deltas,
  bytes calldata callbackData,
  bytes calldata extensionData
- ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
+ ) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

## Proof of Concept
1. Pool admin calls `pausePool(pool)` (sets `pauseLevel = 1`) due to a detected accounting discrepancy.
2. Alice calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, ...)`.
3. The adder calls `pool.addLiquidity(...)` — no `whenNotPaused` check, execution proceeds.
4. `LiquidityLib.addLiquidity` mints shares against the current (potentially corrupted) `binTotals` / `_binStates`.
5. The pool issues `metricOmmModifyLiquidityCallback`; the adder calls `pay()` pulling Alice's tokens into the paused pool.
6. Admin resolves the accounting bug with a haircut on bin balances, then unpauses.
7. Alice calls `removeLiquidity`; she recovers fewer tokens than she deposited — direct loss of principal.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
