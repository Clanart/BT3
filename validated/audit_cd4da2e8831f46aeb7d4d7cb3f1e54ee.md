### Title
Missing Factory Pool Validation in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User Tokens - (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

### Summary

`MetricOmmPoolLiquidityAdder` does not validate the caller-supplied `pool` address against the factory before storing it as the trusted callback counterparty. The router (`MetricOmmSwapRouterBase`) enforces this check via `_requireFactoryPool`, but the liquidity adder explicitly omits it. A user tricked into calling `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address will have their pre-approved tokens drained up to the `maxAmountToken0`/`maxAmountToken1` caps they supplied.

### Finding Description

`MetricOmmSwapRouterBase._setNextCallbackContext` always calls `_requireFactoryPool(pool)` before storing the pool in transient context: [1](#0-0) 

```solidity
function _setNextCallbackContext(address pool, ...) internal {
    _requireFactoryPool(pool);          // ← factory check enforced
    TransientCallbackPool.set(...);
}
```

`MetricOmmPoolLiquidityAdder._setPayContext` stores the pool with **no factory check**: [2](#0-1) 

The contract's own NatSpec acknowledges this gap: [3](#0-2) 

> "This contract does not verify the pool against the factory; a malicious pool can request token pulls up to the caller-provided max caps during callback settlement."

The callback then pays the caller-supplied pool unconditionally once `msg.sender == expectedPool` (which is trivially satisfied when the malicious pool is the one calling back): [4](#0-3) 

### Impact Explanation

A victim who has approved `MetricOmmPoolLiquidityAdder` to spend their tokens and calls `addLiquidityExactShares(maliciousPool, ..., max0, max1, ...)` will lose up to `max0` units of token0 and `max1` units of token1. The loss is bounded only by the caps the user provides and their ERC-20 allowance — both of which are set by the user in good faith expecting a legitimate pool.

### Likelihood Explanation

The attack requires a user to supply a malicious pool address, which can happen via a malicious or compromised frontend, a phishing UI, or a pool address that looks legitimate but is not factory-registered. Because the router enforces the factory check and the liquidity adder does not, users who understand the router's safety model will incorrectly assume the adder provides the same guarantee.

### Recommendation

Add a factory validation call inside `_addLiquidity` (or `_setPayContext`) mirroring the router's `_requireFactoryPool`:

```solidity
function _addLiquidity(address pool, ...) internal returns (...) {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool);   // ← add this
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    ...
}
```

This requires storing the factory address as an immutable in `MetricOmmPoolLiquidityAdder`, analogous to how `MetricOmmSwapRouterBase` stores `FACTORY`.

### Proof of Concept

1. Attacker deploys `MaliciousPool` implementing `addLiquidity(owner, salt, deltas, callbackData, extensionData)` to immediately call back `metricOmmModifyLiquidityCallback(max0, max1, abi.encode(uint8(1)))` (KIND_PAY = 1).
2. Victim approves `MetricOmmPoolLiquidityAdder` for `max0` token0 and `max1` token1.
3. Victim (or a malicious frontend on their behalf) calls:
   ```solidity
   adder.addLiquidityExactShares(
       MaliciousPool, victim, salt, deltas, max0, max1, ""
   );
   ```
4. `_setPayContext(MaliciousPool, victim, max0, max1)` stores `MaliciousPool` as `expectedPool`.
5. `MaliciousPool.addLiquidity(...)` fires; it calls back `metricOmmModifyLiquidityCallback(max0, max1, abi.encode(uint8(1)))`.
6. `msg.sender == expectedPool` → passes. `amount0Delta <= max0 && amount1Delta <= max1` → passes.
7. `pay(token0, victim, MaliciousPool, max0)` and `pay(token1, victim, MaliciousPool, max1)` execute.
8. Victim loses up to `max0 + max1` tokens; attacker receives them in `MaliciousPool`. [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L152-178)
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
