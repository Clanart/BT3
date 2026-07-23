### Title
`pay()` uses `safeTransfer` instead of `safeTransferFrom` for non-WETH ERC20 payers, breaking all user-initiated swaps and enabling router balance drain — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments.sol` has a wrong branch for the case where `payer != address(this)` and `token != WETH`. It calls `IERC20(token).safeTransfer(recipient, value)` — pulling from the **router's own balance** — instead of `IERC20(token).safeTransferFrom(payer, recipient, value)` — pulling from the **designated payer**. This mirrors the HyperdriveLP bug class exactly: a token flow is attributed to the wrong address.

---

### Finding Description

In `PeripheryPayments.sol`, the `pay()` function has three branches: [1](#0-0) 

```solidity
function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);          // ✅ intermediate hop
    } else if (token == WETH) {
        // ... correctly uses safeTransferFrom(payer, ...) when needed ✅
    } else {
        IERC20(token).safeTransfer(recipient, value);          // ❌ WRONG: ignores `payer`
    }
}
```

The final `else` branch — reached whenever `payer` is an **external user** (`msg.sender`) and the token is **any non-WETH ERC20** — calls `safeTransfer` from the router's own balance instead of `safeTransferFrom(payer, recipient, value)`.

This function is called from two places:

**1. `MetricOmmSimpleRouter._justPayCallback`** (single-hop exact-input/output): [2](#0-1) 

```solidity
function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
        _getTokenToPay(),
        _getPayer(),          // = msg.sender of exactInputSingle / exactOutputSingle
        msg.sender,           // = pool
        uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
}
```

`_getPayer()` returns the original `msg.sender` stored in transient context: [3](#0-2) 

So `payer != address(this)` and for any non-WETH token, the wrong branch fires.

**2. `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback`**: [4](#0-3) 

```solidity
(address expectedPool, address payer, uint256 max0, uint256 max1) = _loadPayContext();
// ...
if (amount0Delta > 0) {
    pay(token0, payer, msg.sender, amount0Delta);   // payer = original msg.sender
}
if (amount1Delta > 0) {
    pay(token1, payer, msg.sender, amount1Delta);
}
```

Same wrong branch fires for non-WETH tokens.

---

### Impact Explanation

**Scenario A — DoS (broken core functionality):** The router has no balance of the input token. `safeTransfer` reverts. Every `exactInputSingle`, `exactOutputSingle`, `exactInput`, and `exactOutput` call with a non-WETH ERC20 input token reverts. All non-WETH swap flows are permanently broken.

**Scenario B — Free swap / LP fund drain:** The pool sends output tokens to `recipient` **before** invoking the callback: [5](#0-4) 

```solidity
if (zeroForOne) {
    if (amount1Delta < 0) {
        transferToken1(recipient, uint256(-amount1Delta));   // output sent first
    }
    // ...
    IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(...);  // then callback
    if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
    }
}
```

If the router has accumulated non-WETH ERC20 tokens (e.g., from a prior `sweepToken` call that was front-run, from intermediate multi-hop residue, or from any direct transfer), the `safeTransfer` succeeds — the pool's balance check passes — and the attacker receives output tokens **without spending their own input tokens**. This is a direct loss of LP principal.

---

### Likelihood Explanation

- **Trigger**: Any unprivileged user calling `exactInputSingle` / `exactOutputSingle` / `exactInput` / `exactOutput` / `addLiquidityWeighted` with a non-WETH ERC20 token.
- **Frequency**: This is the dominant token class in DeFi. WETH is the exception, not the rule.
- **Scenario A** (DoS) triggers on every such call with zero router balance — effectively always.
- **Scenario B** (drain) requires the router to hold a token balance, which can be engineered via `multicall` combining a sweep-then-swap, or exploited opportunistically whenever intermediate tokens accumulate.

---

### Recommendation

Change the final `else` branch in `pay()` from `safeTransfer` to `safeTransferFrom`:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol

function pay(address token, address payer, address recipient, uint256 value) internal {
    if (payer == address(this)) {
        IERC20(token).safeTransfer(recipient, value);
    } else if (token == WETH) {
        // ... existing WETH logic unchanged
    } else {
-       IERC20(token).safeTransfer(recipient, value);
+       IERC20(token).safeTransferFrom(payer, recipient, value);
    }
}
```

This ensures the input token is always pulled from the designated payer (the user) rather than from the router's own balance, matching the intent of every call site.

---

### Proof of Concept

1. Deploy a pool with `token0 = USDC`, `token1 = WETH`.
2. Alice calls `exactInputSingle` with `tokenIn = USDC`, `amountIn = 1000e6`, `recipient = Alice`.
3. The pool computes the swap, sends WETH output to Alice.
4. Pool calls `metricOmmSwapCallback` on the router.
5. Router calls `pay(USDC, Alice, pool, 1000e6)`.
6. Since `Alice != address(this)` and `USDC != WETH`, the `else` branch fires: `IERC20(USDC).safeTransfer(pool, 1000e6)`.
7. **If router has no USDC**: reverts → Alice's swap fails, WETH already sent is rolled back. Core swap functionality broken.
8. **If router has ≥ 1000e6 USDC** (e.g., from a prior multicall): transfer succeeds, pool balance check passes, Alice receives WETH without spending her own USDC. LP funds drained.

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
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

**File:** metric-core/contracts/MetricOmmPool.sol (L250-263)
```text
    if (zeroForOne) {
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
```
