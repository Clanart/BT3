Audit Report

## Title
`addLiquidity` bypasses pool pause restriction, allowing fund deposits into a paused pool — (File: metric-core/contracts/MetricOmmPool.sol)

## Summary
`MetricOmmPool` exposes a `whenNotPaused` modifier backed by `_checkNotPaused()` that reverts when `pauseLevel != 0`. `swap` is correctly guarded by this modifier, but `addLiquidity` carries only `nonReentrant`, meaning any caller can deposit real tokens into a paused pool. Users interacting through `MetricOmmPoolLiquidityAdder` will trigger this bypass inadvertently.

## Finding Description
The `whenNotPaused` modifier is defined at [1](#0-0)  and calls `_checkNotPaused()` which reverts when `pauseLevel != 0` [2](#0-1) .

`swap` applies this guard: [3](#0-2) 

`addLiquidity` does not — it carries only `nonReentrant(PoolActions.ADD_LIQUIDITY)`: [4](#0-3) 

`removeLiquidity` is similarly unguarded: [5](#0-4) 

The exploit path through `MetricOmmPoolLiquidityAdder.addLiquidityExactShares` → `pool.addLiquidity` → `metricOmmModifyLiquidityCallback` → `pay()` is fully reachable with no privilege: [6](#0-5) [7](#0-6) 

## Impact Explanation
When the pool is paused due to an accounting discrepancy or active exploit, new shares are minted against potentially corrupted `binTotals` / `_binStates`. When the pool is later unpaused and balances are reconciled (possibly with a haircut), the depositor's shares are worth less than the tokens they transferred in — direct loss of principal. This meets the "direct loss of user principal" threshold under the allowed impact gate.

## Likelihood Explanation
Medium. The pool must first be paused by an admin or protocol action, but once paused the bypass is unconditional and requires no special privilege. Any EOA or contract can call `addLiquidity` directly or via the liquidity adder. Users interacting through aggregators or the `MetricOmmPoolLiquidityAdder` will trigger the bypass without awareness of the pause state.

## Recommendation
Add `whenNotPaused` to `addLiquidity` (and consistently to `removeLiquidity` if the intent is to halt all fund flows during a pause):

```solidity
function addLiquidity(
  address owner,
  uint80 salt,
  LiquidityDelta calldata deltas,
  bytes calldata callbackData,
  bytes calldata extensionData
) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

## Proof of Concept
1. Pool admin calls `pausePool(pool)` setting `pauseLevel = 1` due to a detected accounting discrepancy.
2. Alice calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, ...)`.
3. The adder calls `pool.addLiquidity(...)` — no `whenNotPaused` check, execution proceeds.
4. `LiquidityLib.addLiquidity` mints shares against the current (potentially corrupted) `binTotals` and `_binStates`, issues `metricOmmModifyLiquidityCallback` back to the adder, which calls `pay()` pulling Alice's tokens into the pool.
5. When the pool is unpaused after the accounting fix (which may include a haircut on bin balances), Alice's shares are redeemable for fewer tokens than she deposited — direct loss of principal.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L152-179)
```text
  function metricOmmModifyLiquidityCallback(uint256 amount0Delta, uint256 amount1Delta, bytes calldata callbackData)
    external
    override
  {
    uint8 kind = abi.decode(callbackData, (uint8));
    if (kind == KIND_PROBE) {
      revert LiquidityProbe(amount0Delta, amount1Delta);
    }
    if (kind != KIND_PAY) revert InvalidCallbackKind();

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
  }
```
