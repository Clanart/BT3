### Title
`addLiquidity` bypasses pool pause restriction, allowing fund deposits into a paused pool — (File: metric-core/contracts/MetricOmmPool.sol)

### Summary
`MetricOmmPool.swap` is protected by the `whenNotPaused` modifier, but `addLiquidity` is not. When the pool is paused — whether due to a bug, an ongoing attack, or a price-provider failure — any caller can still invoke `addLiquidity` and deposit real tokens into the compromised pool, directly exposing new principal to whatever risk triggered the pause.

### Finding Description
`MetricOmmPool` exposes a `pauseLevel` state variable (0 = active, 1 = admin-paused, 2 = protocol-paused) and a `whenNotPaused` modifier that reverts when `pauseLevel != 0`. [1](#0-0) 

`swap` correctly applies this guard: [2](#0-1) 

`addLiquidity` does not: [3](#0-2) 

The function carries only `nonReentrant`. There is no pause check. A user who calls `addLiquidity` (directly or via `MetricOmmPoolLiquidityAdder`) while `pauseLevel > 0` will have their tokens pulled from their wallet and deposited into the pool without any revert. [4](#0-3) 

### Impact Explanation
The pause mechanism exists precisely to halt fund flows when the pool is in a dangerous state. If the pool is paused because:

1. **An accounting bug is active** — new shares minted during the pause will be computed against corrupted `binTotals` / `_binStates`, causing the depositor to receive inflated or deflated shares. When they later call `removeLiquidity`, they recover a different amount than deposited — direct loss of principal.
2. **An ongoing attack is in progress** — the attacker can continue to interact with pool state (via `addLiquidity`) even after the admin triggers a pause, defeating the emergency stop.
3. **The price provider is returning bad prices** — while `addLiquidity` itself does not call the price provider, the pool's bin cursor (`curBinIdx`, `curPosInBin`) may already be in a corrupted position from a prior bad-price swap; new liquidity deposited into those bins is immediately at risk when the pool is unpaused and trading resumes at the stale cursor.

In all three scenarios the depositor suffers a direct loss of the tokens they transferred in.

### Likelihood Explanation
Medium. The pool must first be paused (an admin or protocol action), but once paused the bypass is unconditional and requires no special privilege — any EOA or contract can call `addLiquidity`. Users who are unaware of the pause (e.g., interacting through an aggregator or the `MetricOmmPoolLiquidityAdder`) will trigger the bypass inadvertently.

### Recommendation
Add `whenNotPaused` to `addLiquidity`, mirroring the guard already present on `swap`:

```solidity
- function addLiquidity(
-   address owner,
-   uint80 salt,
-   LiquidityDelta calldata deltas,
-   bytes calldata callbackData,
-   bytes calldata extensionData
- ) external nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
+ function addLiquidity(
+   address owner,
+   uint80 salt,
+   LiquidityDelta calldata deltas,
+   bytes calldata callbackData,
+   bytes calldata extensionData
+ ) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) returns (uint256 amount0Added, uint256 amount1Added) {
```

### Proof of Concept

1. Pool admin calls `MetricOmmPoolFactory.pausePool(pool)` (sets `pauseLevel = 1`) because an accounting discrepancy is detected.
2. Alice calls `MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, ...)` — the adder forwards to `pool.addLiquidity(...)`.
3. `addLiquidity` has no `whenNotPaused` check; it proceeds, calls `LiquidityLib.addLiquidity`, which issues a `metricOmmModifyLiquidityCallback` back to the adder, which pulls Alice's tokens via `pay()`.
4. Alice's tokens are now inside the paused, potentially corrupted pool.
5. When the pool is unpaused and the accounting bug is resolved (possibly with a haircut on bin balances), Alice's shares are worth less than she deposited — direct loss of principal. [5](#0-4) [6](#0-5)

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
