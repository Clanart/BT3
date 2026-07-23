Audit Report

## Title
Unvalidated `pool` Parameter in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain Approved User Tokens via Callback - (File: metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol)

## Summary

`MetricOmmPoolLiquidityAdder` accepts a caller-supplied `pool` address and stores it as the sole authorized callback caller in transient storage without validating it against the factory registry. A malicious contract passed as `pool` can invoke `metricOmmModifyLiquidityCallback` on itself, pass the `msg.sender == expectedPool` guard, return attacker-chosen token addresses from `getImmutables()`, and drain up to `maxAmountToken0`/`maxAmountToken1` of any tokens from the victim's wallet via `pay()`.

## Finding Description

The constructor takes only `weth` — no factory reference is stored: [1](#0-0) 

The contract explicitly documents the missing guard: [2](#0-1) 

Every entry point (`addLiquidityExactShares`, `addLiquidityWeighted`) passes the raw caller-supplied `pool` into `_addLiquidity`, which stores it as the authorized callback caller via `_setPayContext`: [3](#0-2) [4](#0-3) 

The callback authenticates the caller solely by comparing `msg.sender` to the stored `expectedPool` — which is the attacker-controlled address: [5](#0-4) 

After passing that check, the callback calls `IMetricOmmPool(msg.sender).getImmutables()` to obtain `token0`/`token1`, then calls `pay()` with those attacker-controlled addresses: [6](#0-5) 

Because the malicious pool controls both the `getImmutables()` return values and the `amount0Delta`/`amount1Delta` it reports in the callback, it can specify any token addresses and request up to the victim's stated caps. By contrast, `MetricOmmSwapRouterBase._setNextCallbackContext` always calls `_requireFactoryPool(pool)` before storing any pool in transient context: [7](#0-6) [8](#0-7) 

## Impact Explanation

A victim who has approved `MetricOmmPoolLiquidityAdder` for any ERC-20 token (a prerequisite for normal use) and is tricked into calling any `addLiquidity*` variant with a malicious `pool` address loses up to `maxAmountToken0` of one token and `maxAmountToken1` of another token in a single transaction. The attacker receives those tokens directly at the malicious pool address. This is a direct, irreversible loss of user principal with no protocol-level recovery path — meeting the Critical/High threshold for direct loss of user funds.

## Likelihood Explanation

`MetricOmmPoolLiquidityAdder` is a shared periphery contract that users must pre-approve for large token amounts before adding liquidity. Once approved, a single phishing transaction (e.g., a spoofed UI presenting a fake pool address) is sufficient to trigger the drain. No privileged access, no special token behavior, and no complex setup is required beyond deploying a ~30-line malicious contract. The attack is replayable against every victim who has an outstanding approval.

## Recommendation

Add factory validation to every `addLiquidity*` entry point, mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
// In MetricOmmPoolLiquidityAdder constructor, store the factory:
IMetricOmmPoolFactory internal immutable FACTORY;

constructor(address weth, address factory) PeripheryPayments(weth) {
    if (factory == address(0)) revert InvalidFactory();
    FACTORY = IMetricOmmPoolFactory(factory);
}

// In _addLiquidity (and the weighted probe path), add before _setPayContext:
if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
```

This is the same guard used by `MetricOmmSwapRouterBase._requireFactoryPool` and eliminates the entire attack surface at negligible gas cost.

## Proof of Concept

**Execution trace:**
1. `addLiquidityExactShares(mp, victim, ...)` → `_addLiquidity` → `_setPayContext(mp, victim, maxAmt0, maxAmt1)`
2. `IMetricOmmPoolActions(mp).addLiquidity(...)` → `MaliciousPool.addLiquidity` executes
3. Malicious pool calls `adder.metricOmmModifyLiquidityCallback(victimBal0, victimBal1, KIND_PAY)`
4. Callback: `msg.sender (mp) == expectedPool (mp)` ✓ — guard passes
5. `mp.getImmutables()` returns attacker-chosen `token0`, `token1`
6. `pay(token0, victim, mp, victimBal0)` → `safeTransferFrom(victim, mp, victimBal0)` ✓
7. `pay(token1, victim, mp, victimBal1)` → `safeTransferFrom(victim, mp, victimBal1)` ✓
8. Victim's tokens are now at `mp`; attacker withdraws them.

A Foundry integration test can deploy `MaliciousPool` as described in the PoC, have a test address approve the adder, call `addLiquidityExactShares` with the malicious pool, and assert that the victim's token balances are zero and the malicious pool holds them.

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L192-195)
```text
  ) internal returns (uint256 amount0Added, uint256 amount1Added) {
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    try IMetricOmmPoolActions(pool)
      .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) returns (
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
