### Title
Unvalidated `pool` Address in `MetricOmmPoolLiquidityAdder` Allows Malicious Pool to Drain Caller-Approved Tokens — (`metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol`)

---

### Summary

`MetricOmmPoolLiquidityAdder` accepts an arbitrary `pool` address in every public entry-point without verifying it against the factory registry. `MetricOmmSimpleRouter` enforces `_requireFactoryPool(pool)` before every swap, but the liquidity adder has no equivalent guard. A malicious pool can exploit the `metricOmmModifyLiquidityCallback` to pull the caller's approved tokens into itself by returning attacker-controlled token addresses from `getImmutables()` and requesting amounts up to the caller-supplied caps.

---

### Finding Description

Every public `addLiquidityExactShares` and `addLiquidityWeighted` overload in `MetricOmmPoolLiquidityAdder` forwards the caller-supplied `pool` directly to `_addLiquidity`, which stores it as the authoritative callback caller in transient storage and immediately calls `pool.addLiquidity(...)`.

```solidity
// MetricOmmPoolLiquidityAdder.sol – _addLiquidity (line 193)
_setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
try IMetricOmmPoolActions(pool)
  .addLiquidity(positionOwner, salt, deltas, abi.encode(KIND_PAY), extensionData) ...
```

No factory check is performed. The NatSpec on line 19–21 explicitly acknowledges this:

> "This contract does not verify the pool against the factory; a malicious pool can request token pulls up to the caller-provided max caps during callback settlement."

When the malicious pool's `addLiquidity` fires the callback, `metricOmmModifyLiquidityCallback` passes the caller-identity check (`msg.sender == expectedPool`) because the malicious pool **is** the stored expected pool. The callback then calls `IMetricOmmPool(msg.sender).getImmutables()` to obtain `token0`/`token1`, which the malicious pool controls entirely, and pays those tokens from the victim's wallet:

```solidity
// MetricOmmPoolLiquidityAdder.sol – metricOmmModifyLiquidityCallback (lines 169–177)
PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
address token0 = imm.token0;
address token1 = imm.token1;
if (amount0Delta > 0) pay(token0, payer, msg.sender, amount0Delta);
if (amount1Delta > 0) pay(token1, payer, msg.sender, amount1Delta);
```

The `pay` function issues `safeTransferFrom(payer, maliciousPool, amount)`, draining the caller's balance of whichever ERC-20 tokens the malicious pool names, up to `maxAmountToken0` / `maxAmountToken1`.

By contrast, `MetricOmmSwapRouterBase._setNextCallbackContext` always calls `_requireFactoryPool(pool)` first:

```solidity
// MetricOmmSwapRouterBase.sol – lines 29–32
function _setNextCallbackContext(address pool, ...) internal {
  _requireFactoryPool(pool);
  TransientCallbackPool.set(pool, callbackMode, payer, tokenToPay);
}
```

The inconsistency means the router path is safe while the liquidity path is not.

---

### Impact Explanation

A malicious pool can drain up to `maxAmountToken0` of any ERC-20 token and `maxAmountToken1` of any other ERC-20 token from the caller's wallet, provided the caller has previously approved those tokens to `MetricOmmPoolLiquidityAdder`. The caller believes they are depositing into a specific pool with specific tokens; the malicious pool substitutes arbitrary tokens via `getImmutables()`. This is a direct loss of user principal with no recovery path.

---

### Likelihood Explanation

The attack requires a user to call `addLiquidityExactShares` or `addLiquidityWeighted` with a malicious pool address. This is achievable through:

1. **Social engineering / phishing**: An attacker deploys a contract that looks like a legitimate pool and advertises it. Users who have approved tokens to `MetricOmmPoolLiquidityAdder` and call it with the attacker's address lose funds.
2. **Front-end substitution**: A compromised or malicious front-end substitutes the pool address.
3. **User error**: A typo or copy-paste mistake pointing to a contract that happens to implement the callback interface maliciously.

Because `MetricOmmPoolLiquidityAdder` is a shared periphery contract that users approve tokens to in advance, the attack surface is persistent across all users who have outstanding approvals.

---

### Recommendation

Add a factory validation check at the top of `_addLiquidity` (or in each public entry-point), mirroring the pattern already used in `MetricOmmSwapRouterBase`:

```solidity
function _addLiquidity(address pool, ...) internal returns (...) {
    if (!FACTORY.isPool(pool)) revert InvalidPool(pool);
    _setPayContext(pool, payer, maxAmountToken0, maxAmountToken1);
    ...
}
```

This requires storing the factory address as an immutable in `MetricOmmPoolLiquidityAdder`, exactly as `MetricOmmSwapRouterBase` does with `IMetricOmmPoolFactory internal immutable FACTORY`.

---

### Proof of Concept

1. Attacker deploys `MaliciousPool` implementing `IMetricOmmPoolActions.addLiquidity` and `IMetricOmmPool.getImmutables`.
   - `getImmutables()` returns `token0 = WBTC`, `token1 = LINK` (tokens the victim holds and has approved).
   - `addLiquidity(...)` calls back `metricOmmModifyLiquidityCallback(max0, max1, abi.encode(KIND_PAY))`.

2. Victim has approved `MetricOmmPoolLiquidityAdder` for WBTC and LINK.

3. Attacker tricks victim into calling:
   ```solidity
   liquidityAdder.addLiquidityExactShares(
       maliciousPool,
       victim,          // owner
       0,               // salt
       deltas,
       max0,            // e.g. 1 WBTC
       max1,            // e.g. 1000 LINK
       ""
   );
   ```

4. `_addLiquidity` stores `maliciousPool` as expected callback caller and calls `maliciousPool.addLiquidity(...)`.

5. `MaliciousPool.addLiquidity` immediately calls back `metricOmmModifyLiquidityCallback(max0, max1, abi.encode(KIND_PAY))`.

6. Callback passes `msg.sender == expectedPool` check. `getImmutables()` returns WBTC/LINK. `pay(WBTC, victim, maliciousPool, max0)` and `pay(LINK, victim, maliciousPool, max1)` execute via `safeTransferFrom`.

7. Attacker receives 1 WBTC + 1000 LINK from the victim's wallet. Victim receives zero liquidity. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
  }
```
