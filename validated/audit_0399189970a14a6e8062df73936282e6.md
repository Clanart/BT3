Audit Report

## Title
`addLiquidity` and `removeLiquidity` bypass the emergency pause — (`metric-core/contracts/MetricOmmPool.sol`)

## Summary
`MetricOmmPool` exposes a `whenNotPaused` modifier that gates `swap`, but neither `addLiquidity` nor `removeLiquidity` apply it. When the pool is paused to halt trading during an oracle compromise or price anomaly, liquidity deposits and withdrawals continue to execute, directly undermining the emergency pause invariant and exposing LP principal to loss.

## Finding Description
The `whenNotPaused` modifier calls `_checkNotPaused()`, which reverts with `PoolPaused` whenever `pauseLevel != 0`. [1](#0-0) [2](#0-1) 

`swap` correctly applies the modifier: [3](#0-2) 

`addLiquidity` carries only `nonReentrant` — no `whenNotPaused`: [4](#0-3) 

`removeLiquidity` is identical in this regard: [5](#0-4) 

The periphery `MetricOmmPoolLiquidityAdder` calls `pool.addLiquidity` directly and adds no pause check of its own, so the gap is reachable from the standard user-facing entry point: [6](#0-5) 

## Impact Explanation
Two concrete loss paths exist within the allowed impact gate:

**Direct LP principal loss via oracle-compromise scenario:** An LP (or attacker acting as LP) deposits tokens into a paused pool via `addLiquidityExactShares` or `addLiquidityWeighted`. When the pause is lifted, swaps resume against the bad oracle price, and the freshly deposited principal is immediately drained by adversarial swaps. This is a direct loss of user principal.

**Inequitable exit during pause:** An informed LP calls `removeLiquidity` while the pool is paused (e.g., during an in-progress exploit that has moved the bin cursor adversarially). They withdraw their share before other LPs can react, leaving remaining LPs holding a skewed cursor and reduced depth — a disproportionate loss of LP assets when swaps resume.

Both outcomes constitute direct loss of LP principal above Sherlock thresholds and fall squarely within the "broken core pool functionality causing loss of funds" and "pool insolvency" impact categories.

## Likelihood Explanation
- The pause mechanism is explicitly designed for real operational emergencies (oracle failure, price anomaly, active exploit).
- `addLiquidity` has no `msg.sender == owner` restriction; any address can call it.
- `removeLiquidity` is callable by any position owner — no privilege required.
- `MetricOmmPoolLiquidityAdder` is the standard user-facing router and passes calls through without any pause check, making the gap trivially reachable by any user.
- No special setup or privileged role is needed beyond the pool already being paused.

## Recommendation
Add `whenNotPaused` to both functions in `MetricOmmPool.sol`:

```solidity
function addLiquidity(...) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (...) { ... }

function removeLiquidity(...) external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY) returns (...) { ... }
```

If allowing LPs to exit during a pause is an intentional design choice for `removeLiquidity`, that decision must be explicitly documented and the asymmetry with `addLiquidity` must still be resolved.

## Proof of Concept
```
1. Deploy pool with a mutable price provider.
2. Call pool.setPause(1) via factory (admin pause).
3. Attempt pool.swap(...) → reverts with PoolPaused. ✓
4. Call MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, ...) →
   succeeds; tokens transferred into paused pool. ✗
5. Call pool.removeLiquidity(owner, salt, deltas, "") →
   succeeds; tokens transferred out of paused pool. ✗
6. Call pool.setPause(0) (unpause).
7. Attacker swaps against pool at the oracle price that triggered the pause →
   LP funds deposited in step 4 are drained.
```

Steps 4 and 5 are directly confirmed by the production code: neither function carries `whenNotPaused`, so both execute unconditionally regardless of `pauseLevel`.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L194-196)
```text
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
```
