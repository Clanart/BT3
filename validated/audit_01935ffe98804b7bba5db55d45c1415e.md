### Title
`addLiquidity` and `removeLiquidity` lack `whenNotPaused` modifier, allowing liquidity operations to bypass pool pause — (File: metric-core/contracts/MetricOmmPool.sol)

---

### Summary

`MetricOmmPool.swap` is guarded by `whenNotPaused`, but `addLiquidity` and `removeLiquidity` carry no such guard. When the pool is paused at level 1 (admin) or level 2 (protocol) to halt activity during a security incident, any unprivileged caller can still add or remove liquidity directly on the pool or through the `MetricOmmPoolLiquidityAdder` periphery contract, bypassing the intended freeze.

---

### Finding Description

`MetricOmmPool` defines a `whenNotPaused` modifier that reverts when `pauseLevel != 0`: [1](#0-0) 

`swap` correctly applies this modifier: [2](#0-1) 

However, `addLiquidity` and `removeLiquidity` are `external` with only `nonReentrant` — no `whenNotPaused`: [3](#0-2) 

The `MetricOmmPoolLiquidityAdder` periphery contract calls `addLiquidity` on the pool directly, so the bypass is reachable through the standard user-facing entry points `addLiquidityExactShares` and `addLiquidityWeighted`: [4](#0-3) [5](#0-4) 

The pool documentation describes the pause as intended to disable swaps "and generally 'active' trading" during incidents and upgrades: [6](#0-5) 

The omission of `whenNotPaused` from `addLiquidity` and `removeLiquidity` is inconsistent with this stated intent and with the guard applied to `swap`.

---

### Impact Explanation

**`addLiquidity` without pause guard — direct LP fund loss:**
The pool is paused precisely because its state is unsafe (e.g., oracle is returning a stale or manipulated price, or a bin-accounting bug has been discovered). A user who adds liquidity through `MetricOmmPoolLiquidityAdder` during the pause deposits real tokens into a pool whose price bands or bin cursor are in a corrupted or adversarially-skewed state. When the pool is unpaused, arbitrageurs immediately drain value from those newly-added positions. The LP suffers a direct loss of deposited principal with no recourse.

**`removeLiquidity` without pause guard — LP race and insolvency risk:**
If the pool is paused after an exploit has already inflated certain positions (e.g., via a swap-path bug), the attacker can call `removeLiquidity` before the admin can take further remediation steps. Because `removeLiquidity` only requires `msg.sender == owner`, the attacker exits with inflated proceeds while honest LPs are left with a depleted pool, breaking the solvency invariant that pool balances must cover all LP claims.

---

### Likelihood Explanation

- The pool pause mechanism is a live, permissioned admin action reachable through `MetricOmmPoolFactory.pausePool` (admin) and `protocolPausePool` (protocol owner).
- Once paused, **any** unprivileged address can call `addLiquidity` or `removeLiquidity` directly on the pool, or route through `MetricOmmPoolLiquidityAdder`, with no special role or setup required.
- The `MetricOmmPoolLiquidityAdder` is the standard periphery entry point for LPs; users interacting with it during a pause window (e.g., a pending transaction in the mempool at pause time) will have their transaction succeed when it should revert.

---

### Recommendation

Add the `whenNotPaused` modifier to both `addLiquidity` and `removeLiquidity` in `MetricOmmPool`:

```solidity
function addLiquidity(...) external whenNotPaused nonReentrant(PoolActions.ADD_LIQUIDITY) ...

function removeLiquidity(...) external whenNotPaused nonReentrant(PoolActions.REMOVE_LIQUIDITY) ...
```

This mirrors the fix recommended in the CDPVault report (adding the guard to the underlying function) and ensures the pause level uniformly halts all state-mutating pool operations, not only swaps.

---

### Proof of Concept

```
1. Admin calls MetricOmmPoolFactory.pausePool(pool)
   → pool.pauseLevel = 1

2. pool.swap(...) → reverts with PoolPaused()   ✓ (guarded)

3. MetricOmmPoolLiquidityAdder.addLiquidityExactShares(pool, owner, salt, deltas, max0, max1, ext)
   → internally calls pool.addLiquidity(...)
   → pool.addLiquidity has NO whenNotPaused check
   → call SUCCEEDS, tokens are pulled from user and deposited into the paused/compromised pool

4. Pool is unpaused after incident resolution.
   Arbitrageurs swap against the pool at the skewed price bands.
   LP who added in step 3 suffers immediate loss of deposited principal.
```

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L174-177)
```text
  modifier whenNotPaused() {
    _checkNotPaused();
    _;
  }
```

**File:** metric-core/contracts/MetricOmmPool.sol (L182-212)
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

  /// @inheritdoc IMetricOmmPoolActions
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-207)
```text
  function _addLiquidity(
    address pool,
    address positionOwner,
    uint80 salt,
    LiquidityDelta memory deltas,
    address payer,
    uint256 maxAmountToken0,
    uint256 maxAmountToken1,
    bytes calldata extensionData
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
    ) {
      amount0Added = a0;
      amount1Added = a1;
      _clearPayContext();
    } catch (bytes memory reason) {
      _clearPayContext();
      assembly ("memory-safe") {
        revert(add(reason, 32), mload(reason))
      }
    }
  }
```

**File:** metric-core/docs/POOL_CONFIGURATION_AND_MANAGEMENT.md (L143-148)
```markdown
### 5.2 Pausing (level 1)

| Function                | What it does                              | Guidelines                                                                                          |
| ----------------------- | ----------------------------------------- | --------------------------------------------------------------------------------------------------- |
| **`pausePool(pool)`**   | Sets pause level **1** (from **0** only). | Disables swaps (and generally “active” trading); use for incidents, upgrades, or market conditions. |
| **`unpausePool(pool)`** | Sets level **0** (from **1** only).       | After protocol releases an L2 pause, admin **cannot** unpause directly to **0**—see below.          |
```
