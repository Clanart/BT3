Audit Report

## Title
Unvalidated `pool` Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain Caller-Approved Tokens — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address in every public entry-point without verifying it against the factory registry. Because `_addLiquidity` stores the caller-supplied pool as the authoritative callback caller and immediately invokes it, a malicious pool passes the `msg.sender == expectedPool` guard in `metricOmmModifyLiquidityCallback`, returns attacker-controlled token addresses from `getImmutables()`, and causes `pay` to execute `safeTransferFrom(victim, maliciousPool, amount)` for any ERC-20 the victim has approved to this contract.

## Finding Description

Every public `addLiquidityExactShares` and `addLiquidityWeighted` overload forwards the caller-supplied `pool` directly to `_addLiquidity` with no factory check:

```solidity
// MetricOmmPoolLiquidityAdder.sol – _addLiquidity (line 193)
_setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
try IMetricOmmPoolActions(pool)
  .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) ...
``` [1](#0-0) 

The NatSpec at lines 19–21 explicitly acknowledges this: [2](#0-1) 

When the malicious pool's `addLiquidity` fires the callback, `metricOmmModifyLiquidityCallback` passes the caller-identity check at line 164 because the malicious pool **is** the stored expected pool. The callback then calls `IMetricOmmPool(msg.sender).getImmutables()` at line 169 to obtain `token0`/`token1`, which the malicious pool controls entirely:

```solidity
// lines 162–177
(address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
if (msg.sender != expectedPool) revert InvalidCallbackCaller(...);   // passes
PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables(); // attacker-controlled
address token0 = imm.token0;
address token1 = imm.token1;
if (amount0Delta > 0) pay(token0, payer, msg.sender, amount0Delta);
if (amount1Delta > 0) pay(token1, payer, msg.sender, amount1Delta);
``` [3](#0-2) 

`pay` then executes `IERC20(token).safeTransferFrom(payer, recipient, value)` for non-WETH tokens, draining the victim's balance: [4](#0-3) 

By contrast, `MetricOmmSwapRouterBase._setNextCallbackContext` always calls `_requireFactoryPool(pool)` before storing context, and `_requireExpectedCallbackCaller` re-checks `FACTORY.isPool(caller)` in the callback: [5](#0-4) [6](#0-5) 

`MetricOmmPoolLiquidityAdder` has no `FACTORY` immutable and no equivalent guard anywhere in its call path.

## Impact Explanation

A malicious pool can drain up to `maxAmountToken0` of any ERC-20 and `maxAmountToken1` of any other ERC-20 from the caller's wallet, provided the caller has previously approved those tokens to `MetricOmmPoolLiquidityAdder`. The victim receives zero liquidity. This is a direct, unrecoverable loss of user principal, meeting the Critical/High threshold under Sherlock contest rules.

## Likelihood Explanation

The attack requires a victim to call `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address. Because `MetricOmmPoolLiquidityAdder` is a shared periphery contract that users approve tokens to in advance, the attack surface is persistent. Delivery vectors include social engineering, a compromised front-end substituting the pool address, or user error. No privileged access is required; any unprivileged attacker can deploy a conforming malicious pool contract.

## Recommendation

Add a factory validation check at the top of `_addLiquidity`, mirroring the pattern in `MetricOmmSwapRouterBase`:

```solidity
function _addLiquidity(address pool, ...) internal returns (...) {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    ...
}
```

This requires storing the factory address as an immutable in `MetricOmmPoolLiquidityAdder`, exactly as `MetricOmmSwapRouterBase` does with `IMetricOmmPoolFactory internal immutable FACTORY`. [7](#0-6) 

## Proof of Concept

1. Attacker deploys `MaliciousPool` implementing `IMetricOmmPoolActions.addLiquidity` and `IMetricOmmPool.getImmutables`. `getImmutables()` returns `token0 = WBTC`, `token1 = LINK`. `addLiquidity(...)` immediately calls back `metricOmmModifyLiquidityCallback(max0, max1, abi.encode(KIND_PAY))`.
2. Victim approves `MetricOmmPoolLiquidityAdder` for WBTC and LINK.
3. Attacker tricks victim into calling `addLiquidityExactShares(maliciousPool, victim, 0, deltas, 1e8 /*1 WBTC*/, 1000e18 /*1000 LINK*/, "")`.
4. `_addLiquidity` stores `maliciousPool` as expected callback caller via `_setPayContext` and calls `maliciousPool.addLiquidity(...)`.
5. `MaliciousPool.addLiquidity` calls back `metricOmmModifyLiquidityCallback(1e8, 1000e18, abi.encode(KIND_PAY))`.
6. Callback: `msg.sender == expectedPool` passes; `getImmutables()` returns WBTC/LINK; `pay(WBTC, victim, maliciousPool, 1e8)` and `pay(LINK, victim, maliciousPool, 1000e18)` execute via `safeTransferFrom`.
7. Attacker receives 1 WBTC + 1000 LINK. Victim receives nothing.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-195)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L20-25)
```text
  IMetricOmmPoolFactory internal immutable FACTORY;

  constructor(address factory) {
    if (factory == address(0)) revert IMetricOmmSimpleRouter.InvalidFactory();
    FACTORY = IMetricOmmPoolFactory(factory);
  }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L82-89)
```text
  function _requireExpectedCallbackCaller(address caller) internal view {
    TransientCallbackPool.requireCaller(caller);
    if (!FACTORY.isPool(caller)) revert IMetricOmmSimpleRouter.InvalidPool(caller);
  }

  function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
  }
```
