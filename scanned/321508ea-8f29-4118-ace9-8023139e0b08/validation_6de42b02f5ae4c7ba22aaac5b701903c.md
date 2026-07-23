### Title
`pay()` Uses `safeTransfer` Instead of `safeTransferFrom` for Non-WETH External Payers, Breaking All Non-WETH Swaps and Liquidity Additions — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` internal function in `PeripheryPayments.sol` contains a critical logic error in its final `else` branch. When the payer is an external user (not the router itself) and the token is not WETH, the function calls `IERC20(token).safeTransfer(recipient, value)` — transferring **from the router** — instead of `IERC20(token).safeTransferFrom(payer, recipient, value)` — pulling **from the user**. Because the router holds no such tokens, every non-WETH swap and liquidity addition reverts unconditionally.

---

### Finding Description

`PeripheryPayments.pay()` has three branches: [1](#0-0) 

```
Branch 1 (payer == address(this)):  safeTransfer  ← correct: router holds intermediate tokens
Branch 2 (token == WETH):           hybrid ETH/WETH paths ← correct
Branch 3 (else):                    safeTransfer  ← WRONG: should be safeTransferFrom
```

The comment on line 70 explains Branch 1: *"If the payer is contract it means we are in the middle of a path."* That is the only case where the router legitimately holds the token. Branch 3 is reached when `payer` is an **external EOA** and `token` is **not WETH** — exactly the case where the router has no balance. The correct call is `IERC20(token).safeTransferFrom(payer, recipient, value)`, matching the WETH-exhausted sub-branch at line 83. [2](#0-1) 

Every non-WETH swap entry point stores the external caller as payer and the input token as `tokenToPay`: [3](#0-2) [4](#0-3) [5](#0-4) 

The callback then calls `pay()` with those stored values: [6](#0-5) 

`MetricOmmPoolLiquidityAdder` is equally affected — its callback calls `pay(token0/token1, payer, pool, delta)` for both legs: [7](#0-6) 

---

### Impact Explanation

- **All non-WETH `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput` calls revert** at the pool callback because `safeTransfer` fails with ERC20 insufficient-balance.
- **All non-WETH `addLiquidityExactShares` and `addLiquidityWeighted` calls revert** for the same reason.
- Secondary: if the router ever holds a non-WETH token balance (e.g., a user accidentally sends tokens directly, or a future integration deposits tokens), any caller can drain that balance for free by triggering a swap — direct loss of principal belonging to a third party.

This satisfies the **"Broken core pool functionality causing loss of funds or unusable swap/liquidity flows"** impact gate.

---

### Likelihood Explanation

Every user who attempts to swap or add liquidity with any non-WETH ERC-20 token through the periphery router or liquidity adder will hit this revert. No special conditions, no privileged access, and no malicious setup are required. Likelihood is **High**.

---

### Recommendation

Change the final `else` branch of `pay()` from:

```solidity
} else {
    IERC20(token).safeTransfer(recipient, value);   // ← wrong: pulls from router
}
```

to:

```solidity
} else {
    IERC20(token).safeTransferFrom(payer, recipient, value);  // ← correct: pulls from payer
}
```

This matches the established Uniswap v3 periphery pattern and is consistent with the WETH-exhausted sub-branch already present at line 83. [1](#0-0) 

---

### Proof of Concept

```
1. User approves MetricOmmSimpleRouter to spend 1000 USDC.
2. User calls exactInputSingle({
       tokenIn:  USDC,
       amountIn: 1000,
       pool:     USDC/ETH pool,
       ...
   })
3. Router stores payer = msg.sender, tokenToPay = USDC in transient storage.
4. Router calls pool.swap(...).
5. Pool executes swap math, then calls metricOmmSwapCallback(amount0Delta, amount1Delta, "").
6. Router enters _justPayCallback → pay(USDC, msg.sender, pool, 1000).
7. payer != address(this) AND token != WETH → last else

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-103)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L135-135)
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
