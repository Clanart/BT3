Audit Report

## Title
LP principal permanently frozen when position owner is blacklisted by pool token — (`metric-core/contracts/MetricOmmPool.sol` / `metric-core/contracts/libraries/LiquidityLib.sol`)

## Summary
`MetricOmmPool.removeLiquidity` enforces `msg.sender == owner` and passes `owner` as the sole token destination to `LiquidityLib.removeLiquidity`, which calls `safeTransfer(owner, ...)` unconditionally. If `owner` is blacklisted by a pool token (USDC or USDT) after depositing, every `removeLiquidity` call reverts and the LP's full principal across all bins is permanently frozen in the pool with no alternative withdrawal path.

## Finding Description
`MetricOmmPool.removeLiquidity` enforces a strict identity check at line 206:

```solidity
if (msg.sender != owner) revert NotPositionOwner();
``` [1](#0-0) 

It then delegates to `LiquidityLib.removeLiquidity`, which transfers recovered tokens directly and unconditionally to `owner`:

```solidity
if (amount0Removed > 0) {
    IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
}
if (amount1Removed > 0) {
    IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
}
``` [2](#0-1) 

There is no `recipient` parameter anywhere in the call chain. The position key is `(owner, salt)` — there is no position-transfer, delegation, or operator-withdrawal mechanism that would allow a different address to receive tokens on behalf of a blacklisted `owner`. The `MetricOmmPoolLiquidityAdder` periphery contract only implements `addLiquidity` flows and provides no `removeLiquidity` path whatsoever. [3](#0-2) 

The interface NatSpec confirms the design intent — tokens go to `owner` with no override: [4](#0-3) 

## Impact Explanation
If a liquidity provider's address is added to the USDC or USDT blacklist after depositing, `safeTransfer(owner, amount)` reverts on every `removeLiquidity` call. Because there is no alternative withdrawal path, no position-transfer function, and no recipient override, the LP's full principal (both token legs across all bins) is permanently locked in the pool. This constitutes a direct, total loss of user principal — a broken core pool withdraw flow — meeting the allowed impact gate for Medium/High severity under Sherlock thresholds.

## Likelihood Explanation
USDC and USDT blacklisting is explicitly in scope per the allowed impact gate ("non-standard ERC20 behavior except USDC/USDT"). Blacklisting of an active LP address is a low-probability but realistic compliance event. No privileged action, malicious setup, or attacker is required; the trigger is a standard USDC/USDT compliance action against the LP's address. The consequence — permanent, total loss of principal — is severe and irreversible.

## Recommendation
Add a `recipient` parameter to `removeLiquidity` (analogous to the `recipient` already present on `swap`). The caller identity check remains on `msg.sender == owner` while the token destination becomes `recipient`:

```solidity
// MetricOmmPool.sol
function removeLiquidity(
    address owner,
    uint80 salt,
    LiquidityDelta calldata deltas,
+   address recipient,
    bytes calldata extensionData
) external nonReentrant(PoolActions.REMOVE_LIQUIDITY) returns (...) {
    if (msg.sender != owner) revert NotPositionOwner();
    ...
    LiquidityLib.removeLiquidity(
-       _liquidityContext(), owner, salt, deltas, ...
+       _liquidityContext(), owner, salt, deltas, recipient, ...
    );
}

// LiquidityLib.sol
- IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
- IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
+ IERC20(ctx.token0).safeTransfer(recipient, amount0Removed);
+ IERC20(ctx.token1).safeTransfer(recipient, amount1Removed);
```

## Proof of Concept
1. Alice (`0xAlice`) provides liquidity to a USDC/WETH Metric OMM pool. Her position key is `(0xAlice, salt)`.
2. USDC blacklists `0xAlice` (e.g., due to a compliance action).
3. Alice calls `removeLiquidity(0xAlice, salt, deltas, "")`.
4. `MetricOmmPool` passes the check `msg.sender == owner` (both are `0xAlice`).
5. `LiquidityLib.removeLiquidity` burns Alice's shares, decrements bin balances, then calls `IERC20(USDC).safeTransfer(0xAlice, amount0Removed)`.
6. USDC's `transfer` reverts because `0xAlice` is blacklisted; the entire transaction reverts and state is rolled back.
7. Alice has no alternative: she cannot delegate removal to another address, cannot specify a different recipient, and there is no position-transfer mechanism. Her USDC principal is permanently frozen in the pool.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L206-206)
```text
    if (msg.sender != owner) revert NotPositionOwner();
```

**File:** metric-core/contracts/libraries/LiquidityLib.sol (L242-247)
```text
      if (amount0Removed > 0) {
        IERC20(ctx.token0).safeTransfer(owner, amount0Removed);
      }
      if (amount1Removed > 0) {
        IERC20(ctx.token1).safeTransfer(owner, amount1Removed);
      }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L49-68)
```text
  // ============ External: liquidity ============

  /// @notice Add liquidity with explicit per-bin shares; reverts in callback if token amounts exceed caps.
  /// @dev `msg.sender` is always the payer for token pulls in callback (stored in transient settlement context).
  /// @param owner Position owner recorded by the pool.
  /// @param maxAmountToken0 Max token0 (native units) the pool may request; inclusive check before pull.
  /// @param maxAmountToken1 Max token1 (native units) the pool may request; inclusive check before pull.
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

**File:** metric-core/contracts/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol (L164-174)
```text
  /// @notice Burn shares across bins for `(owner, salt)` and send underlying tokens to `owner`.
  /// @dev Requires `msg.sender == owner` (`NotPositionOwner` otherwise). No callback: tokens are transferred out directly.
  /// @param owner Must equal `msg.sender`.
  /// @param salt Position salt with `owner`.
  /// @param deltas Parallel arrays of bins and share burns.
  /// @param extensionData Opaque bytes forwarded to liquidity extensions (beforeRemoveLiquidity / afterRemoveLiquidity).
  /// @return amount0Removed Total token0 sent from the pool to `owner` (native).
  /// @return amount1Removed Total token1 sent from the pool to `owner` (native).
  function removeLiquidity(address owner, uint80 salt, LiquidityDelta calldata deltas, bytes calldata extensionData)
    external
    returns (uint256 amount0Removed, uint256 amount1Removed);
```
