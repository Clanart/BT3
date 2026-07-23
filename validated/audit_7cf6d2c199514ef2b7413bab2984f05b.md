The code is clear. Let me trace the full execution path and render a verdict.

---

## Code Trace

**`receive()` guard** — `PeripheryPayments.sol:32-34`

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
```

This blocks plain ETH transfers, but **does not block ETH arriving as `msg.value` in a `payable` function call** like `exactInputSingle`. ETH sent with `exactInputSingle` bypasses `receive()` entirely.

**`pay()` WETH hybrid branch** — `PeripheryPayments.sol:73-84`

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← TOTAL router balance, not caller's ETH
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
}
```

`nativeBalance` is `address(this).balance` — the **entire** router ETH balance, with no per-user accounting or isolation. Any ETH left on the router from a prior user is indistinguishable from the current user's ETH.

**`exactInputSingle` entry point** — `MetricOmmSimpleRouter.sol:67-86`

```solidity
function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    ...
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    ...
}
```

No check that `msg.value == params.amountIn`. A user can send `msg.value > amountIn`; the callback consumes exactly `amountIn` worth of ETH, and the surplus stays on the router.

---

## Vulnerability Verdict

### Title
Unattributed Router ETH Balance Consumed by Subsequent WETH Swaps, Stealing Prior User's Refund — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` as a global pool of ETH when `token == WETH`. It has no per-user attribution. Any ETH left on the router by a prior user (e.g., excess `msg.value` from a WETH swap where `refundETH` was not called) is silently consumed to fund a subsequent user's WETH swap, causing the prior user to lose their refund.

### Finding Description

When a user calls `exactInputSingle` (or any `payable` swap entry point) with `tokenIn = WETH` and `msg.value > amountIn`, the callback `_justPayCallback` calls `pay(WETH, payer, pool, amountIn)`. [1](#0-0) 

Inside `pay`, `nativeBalance = address(this).balance` captures the **total** router ETH balance. [2](#0-1) 

Only `amountIn` ETH is wrapped and forwarded; the surplus `msg.value - amountIn` remains on the router. The `receive()` guard does not prevent this because ETH arriving via `msg.value` in a `payable` call never triggers `receive()`. [3](#0-2) 

When the next user calls `exactInputSingle` with `tokenIn = WETH` and `msg.value = 0`, `pay` reads the leftover ETH as `nativeBalance > 0` and uses it to fund the new swap — either fully (lines 75-77) or partially (lines 78-81) — without pulling from the new user's WETH allowance. [4](#0-3) 

The prior user's ETH is permanently consumed; they receive no refund.

### Impact Explanation

Direct loss of user principal. The prior user's ETH (which should be refundable via `refundETH`) is silently transferred to a pool on behalf of a different user. The amount is bounded only by how much excess ETH the prior user sent. This satisfies the Critical/High direct-loss-of-user-principal gate.

### Likelihood Explanation

Medium. The precondition is that a user sends `msg.value > amountIn` without calling `refundETH` in the same transaction. This is a realistic mistake for users who call `exactInputSingle` directly (not via `multicall` + `refundETH`). No attacker setup is required beyond observing the router's ETH balance and submitting a WETH swap.

### Recommendation

Track only the ETH that arrived with the **current** call, not the total router balance. Capture `msg.value` at the entry point and pass it down to `pay`, or compare `address(this).balance` before and after the call. Alternatively, enforce `msg.value == 0` when `tokenIn != address(0)` (native ETH sentinel) and require a separate native-ETH swap path that validates the exact amount.

### Proof of Concept

```solidity
// 1. User A calls exactInputSingle: tokenIn=WETH, amountIn=100e18, msg.value=150e18
//    → pay() wraps 100e18 ETH, sends WETH to pool
//    → 50e18 ETH remains on router; User A does NOT call refundETH

// 2. Attacker calls exactInputSingle: tokenIn=WETH, amountIn=50e18, msg.value=0
//    → pay(): nativeBalance = 50e18 >= 50e18
//    → wraps 50e18 ETH (User A's), sends WETH to pool
//    → NO transferFrom on attacker's WETH balance

// Assert: User A's 50e18 ETH is gone; attacker paid 0 WETH for a 50e18 WETH swap
```

In Foundry: `deal(address(router), 50e18)` is not needed — simply send excess ETH with the first `exactInputSingle` call and omit `refundETH`. Then call the second swap and assert `IERC20(WETH).balanceOf(attacker)` is unchanged while the pool received the full `amountIn`.

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-74)
```text
      uint256 nativeBalance = address(this).balance;
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L75-81)
```text
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
```
