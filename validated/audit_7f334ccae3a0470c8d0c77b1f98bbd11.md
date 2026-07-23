Audit Report

## Title
Unvalidated `pool` Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain Caller-Approved Tokens — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address in every public entry-point and forwards it directly to `_addLiquidity` without any factory registry check. Because the callback's only caller-identity guard is `msg.sender == expectedPool` — and the malicious pool *is* the stored expected pool — a malicious pool can fire `metricOmmModifyLiquidityCallback` and return attacker-controlled token addresses from `getImmutables()`, causing `pay()` to drain the caller's approved tokens via `safeTransferFrom`.

## Finding Description

Every public overload of `addLiquidityExactShares` and `addLiquidityWeighted` passes the caller-supplied `pool` directly to `_addLiquidity`: [1](#0-0) 

`_addLiquidity` stores the unvalidated pool as the authoritative callback caller in transient storage and immediately calls `pool.addLiquidity(...)`: [2](#0-1) 

In `metricOmmModifyLiquidityCallback`, the only caller check is `msg.sender != expectedPool`. Since the malicious pool *is* the stored expected pool, this check passes. The callback then calls `IMetricOmmPool(msg.sender).getImmutables()` to obtain `token0`/`token1`, which the malicious pool controls entirely: [3](#0-2) 

`pay()` then issues `safeTransferFrom(payer, maliciousPool, amount)` for whichever tokens the malicious pool names. The NatSpec at lines 19–21 explicitly documents this gap rather than guarding against it: [4](#0-3) 

By contrast, `MetricOmmSwapRouterBase._setNextCallbackContext` always calls `_requireFactoryPool(pool)` before storing the pool in transient context: [5](#0-4) 

And `_requireExpectedCallbackCaller` additionally re-checks `FACTORY.isPool(caller)` at callback time: [6](#0-5) 

Neither guard exists anywhere in `MetricOmmPoolLiquidityAdder`. The `MaxAmountExceeded` check (`amount0Delta > max0 || amount1Delta > max1`) only limits the *quantity* drained, not the *token identity*, so the attacker can steal up to the full caller-supplied caps in arbitrary ERC-20 tokens.

## Impact Explanation

A malicious pool can drain up to `maxAmountToken0` of any ERC-20 token and `maxAmountToken1` of any other ERC-20 token from the caller's wallet, provided the caller has previously approved those tokens to `MetricOmmPoolLiquidityAdder`. This is a direct, unrecoverable loss of user principal. The impact is **Critical**: arbitrary ERC-20 theft from any user with outstanding approvals to the shared periphery contract.

## Likelihood Explanation

The attack requires a user to call `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address. This is achievable via social engineering (fake pool advertisement), a compromised front-end substituting the pool address, or user error. Because `MetricOmmPoolLiquidityAdder` is a shared periphery contract that users approve tokens to in advance, the attack surface is persistent across all users with outstanding approvals. No privileged access is required; any unprivileged attacker can deploy a conforming malicious pool contract.

## Recommendation

Add a factory validation check at the top of `_addLiquidity`, mirroring the pattern in `MetricOmmSwapRouterBase`. This requires storing the factory address as an immutable in `MetricOmmPoolLiquidityAdder`:

```solidity
IMetricOmmPoolFactory internal immutable FACTORY;

function _addLiquidity(address pool, ...) internal returns (...) {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    ...
}
```

Optionally, also re-check `FACTORY.isPool(msg.sender)` inside `metricOmmModifyLiquidityCallback` after the `msg.sender == expectedPool` check, as `MetricOmmSwapRouterBase._requireExpectedCallbackCaller` does.

## Proof of Concept

1. Attacker deploys `MaliciousPool` implementing `IMetricOmmPoolActions.addLiquidity` and `IMetricOmmPool.getImmutables`.
   - `getImmutables()` returns `token0 = WBTC`, `token1 = LINK`.
   - `addLiquidity(...)` immediately calls back `metricOmmModifyLiquidityCallback(max0, max1, abi.encode(KIND_PAY))`.

2. Victim has approved `MetricOmmPoolLiquidityAdder` for WBTC and LINK.

3. Victim calls:
   ```solidity
   liquidityAdder.addLiquidityExactShares(
       maliciousPool, victim, 0, deltas, 1e8 /*1 WBTC*/, 1000e18 /*1000 LINK*/, ""
   );
   ```

4. `_addLiquidity` stores `maliciousPool` as expected callback caller (no factory check) and calls `maliciousPool.addLiquidity(...)`.

5. `MaliciousPool.addLiquidity` calls back `metricOmmModifyLiquidityCallback(1e8, 1000e18, abi.encode(KIND_PAY))`.

6. Callback: `msg.sender == expectedPool` passes. `getImmutables()` returns WBTC/LINK. `pay(WBTC, victim, maliciousPool, 1e8)` and `pay(LINK, victim, maliciousPool, 1000e18)` execute via `safeTransferFrom`.

7. Attacker receives 1 WBTC + 1000 LINK. Victim receives zero liquidity.

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-177)
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
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-195)
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
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L82-85)
```text
  function _requireExpectedCallbackCaller(address caller) internal view {
    TransientCallbackPool.requireCaller(caller);
    if (!FACTORY.isPool(caller)) revert IMetricOmmSimpleRouter.InvalidPool(caller);
  }
```
