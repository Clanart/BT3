After reading all the relevant periphery files, I found a concrete vulnerability in `MetricOmmPoolLiquidityAdder`.

---

### Title
Missing Factory Pool Validation in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain User Tokens - (File: `metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

### Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address from the caller without verifying it against the factory registry. A malicious pool can exploit the transient pay-context to pull up to `maxAmountToken0` and `maxAmountToken1` of any token from the victim's wallet during the `metricOmmModifyLiquidityCallback` settlement, resulting in direct loss of user principal.

### Finding Description

`MetricOmmSimpleRouter` enforces factory membership before storing any pool in transient callback context: [1](#0-0) 

```solidity
function _setNextCallbackContext(address pool, ...) internal {
    _requireFactoryPool(pool);   // ← factory gate
    TransientCallbackPool.set(pool, ...);
}
```

`MetricOmmPoolLiquidityAdder._setPayContext` performs **no equivalent check**: [2](#0-1) 

```solidity
function _setPayContext(address pool, address payer, uint256 maxAmountToken0, uint256 maxAmountToken1) internal {
    if (_tloadAddress(T_SLOT_PAY_POOL) != address(0)) revert PayContextAlreadyActive();
    _tstoreAddress(T_SLOT_PAY_POOL, pool);   // ← no factory check
    _tstoreAddress(T_SLOT_PAY_PAYER, payer);
    _tstore(T_SLOT_PAY_MAX0, maxAmountToken0);
    _tstore(T_SLOT_PAY_MAX1, maxAmountToken1);
}
```

The contract's own NatSpec acknowledges this gap: [3](#0-2) 

> "This contract does not verify the pool against the factory; a malicious pool can request token pulls up to the caller-provided max caps during callback settlement."

The callback enforces only that `msg.sender` equals the stored expected pool — which is the attacker-controlled address itself: [4](#0-3) 

```solidity
(address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
if (expectedPool == address(0)) revert CallbackContextNotActive();
if (msg.sender != expectedPool) revert InvalidCallbackCaller(msg.sender, expectedPool);
if (amount0Delta > max0 || amount1Delta > max1) revert MaxAmountExceeded(...);

PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables(); // attacker-controlled
address token0 = imm.token0;
address token1 = imm.token1;
if (amount0Delta > 0) pay(token0, payer, msg.sender, amount0Delta);
if (amount1Delta > 0) pay(token1, payer, msg.sender, amount1Delta);
```

Because `getImmutables()` is called on `msg.sender` (the malicious pool), the attacker controls which tokens are pulled and from whom.

### Impact Explanation

A victim who has approved `MetricOmmPoolLiquidityAdder` for any ERC-20 (e.g., USDC, WETH) and is tricked into calling `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address loses up to `maxAmountToken0` of token0 and `maxAmountToken1` of token1 in a single transaction. The attacker receives the tokens directly at their malicious pool address. This is a direct loss of user principal with no recovery path.

### Likelihood Explanation

Medium. The victim must call the adder with an attacker-supplied pool address. This is achievable via a phishing front-end, a social-engineering attack presenting a fake pool, or a griefing scenario where a user copy-pastes an unverified address. The inconsistency with `MetricOmmSimpleRouter` (which does validate) means users and integrators have a reasonable expectation that the adder applies the same guard, increasing the probability of exploitation.

### Recommendation

Add a factory membership check inside `_setPayContext` (or at the top of each public `addLiquidity*` entry point), mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
function _setPayContext(address pool, address payer, uint256 max0, uint256 max1) internal {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool);   // ← add this
    if (_tloadAddress(T_SLOT_PAY_POOL) != address(0)) revert PayContextAlreadyActive();
    _tstoreAddress(T_SLOT_PAY_POOL, pool);
    ...
}
```

This requires storing the factory address as an immutable in `MetricOmmPoolLiquidityAdder`, analogous to `MetricOmmSwapRouterBase.FACTORY`.

### Proof of Concept

1. Attacker deploys `MaliciousPool` implementing `IMetricOmmPoolActions` and `IMetricOmmPool`.
   - `addLiquidity(...)` immediately calls back `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback(max0, max1, abi.encode(KIND_PAY))`.
   - `getImmutables()` returns attacker-chosen `token0`/`token1` (e.g., USDC and WETH).

2. Victim has approved `MetricOmmPoolLiquidityAdder` for USDC and WETH.

3. Victim is tricked into calling:
   ```solidity
   adder.addLiquidityExactShares(
       MaliciousPool,   // pool
       victim,          // owner
       0,               // salt
       deltas,
       1_000_000e6,     // maxAmountToken0 (1M USDC)
       500e18,          // maxAmountToken1 (500 WETH)
       ""
   );
   ```

4. Execution flow:
   - `_setPayContext(MaliciousPool, victim, 1_000_000e6, 500e18)` — stores malicious pool, no factory check.
   - `MaliciousPool.addLiquidity(...)` is called.
   - `MaliciousPool` re-enters `metricOmmModifyLiquidityCallback(1_000_000e6, 500e18, abi.encode(KIND_PAY))`.
   - `msg.sender == expectedPool` ✓ (both are `MaliciousPool`).
   - `amount0Delta <= max0` ✓, `amount1Delta <= max1` ✓.
   - `pay(USDC, victim, MaliciousPool, 1_000_000e6)` — 1M USDC transferred from victim.
   - `pay(WETH, victim, MaliciousPool, 500e18)` — 500 WETH transferred from victim.

5. Victim loses up to their full approved balance; attacker receives funds at `MaliciousPool`.

### Citations

**File:** metric-periphery/contracts/base/MetricOmmSwapRouterBase.sol (L29-32)
```text
  function _setNextCallbackContext(address pool, uint8 callbackMode, address payer, address tokenToPay) internal {
    _requireFactoryPool(pool);
    TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
  }
```

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L19-21)
```text
/// @dev The caller is responsible for supplying a legitimate pool address and other non-malicious parameters.
///      This contract does not verify the pool against the factory; a malicious pool can request token pulls up to
///      the caller-provided max caps during callback settlement.
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
