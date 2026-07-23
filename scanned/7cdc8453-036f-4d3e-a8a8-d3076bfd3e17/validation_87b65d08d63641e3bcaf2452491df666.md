The `pay` function in `PeripheryPayments.sol` is the critical code to analyze. I have all the information needed.

The vulnerability is real. Here is the full analysis:

---

### Title
Unaccounted Router ETH Balance Silently Subsidizes Subsequent WETH Swaps, Stealing Prior User's Refund — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay` uses `address(this).balance` as a global, unattributed ETH pool when `token == WETH`. Any ETH left on the router from a prior user's `msg.value` overpayment is silently consumed to fund a subsequent user's WETH swap, causing direct loss of the prior user's principal.

### Finding Description

`exactInputSingle` (and `exactInput`) are `payable`. A user legitimately sends `msg.value = Y` intending to swap `amountIn = X` of WETH where `Y > X`. The `pay` function's first branch deposits exactly `X` ETH and transfers WETH to the pool, leaving `Y - X` ETH stranded on the router: [1](#0-0) 

If the user does not call `refundETH()` in the same transaction (e.g., they call `exactInputSingle` standalone rather than via `multicall`), the residual ETH persists on the router with no per-user accounting.

A subsequent caller who invokes `exactInputSingle(tokenIn=WETH, amountIn=Z, msg.value=0)` triggers the callback path: [2](#0-1) 

which calls `pay(WETH, newUser, pool, Z)`. Inside `pay`, `nativeBalance = address(this).balance` now equals the prior user's stranded ETH. The branch at line 75 (`nativeBalance >= value`) or line 78 (`nativeBalance > 0`) fires, consuming the prior user's ETH to fund the new user's swap: [3](#0-2) 

The new user's `transferFrom` is reduced by the consumed amount (or eliminated entirely), and the prior user's ETH is permanently lost.

The `receive()` guard (only accepts ETH from WETH) does not prevent this because `msg.value` in payable function calls bypasses `receive()` entirely: [4](#0-3) 

### Impact Explanation

Direct loss of user principal. The prior user's ETH (the `Y - X` overpayment) is consumed to fund an unrelated user's swap. The prior user receives no WETH and no ETH refund for that amount. The new user receives a fully or partially subsidized swap. This satisfies the Critical/High direct-loss-of-user-principal gate.

### Likelihood Explanation

Moderate. The precondition — ETH stranded on the router — arises naturally whenever a user calls `exactInputSingle` with `msg.value > amountIn` without bundling `refundETH()` in the same multicall. An attacker can monitor the router's ETH balance on-chain and immediately follow with a WETH swap to drain it. No privileged access, malicious pool, or non-standard token is required.

### Recommendation

Track the ETH that belongs to the current transaction's `msg.value` separately from any pre-existing router balance. The simplest fix is to record `address(this).balance - msg.value` at entry and only allow `pay` to use ETH up to `msg.value` (i.e., the amount the current caller explicitly sent). Alternatively, enforce that `pay` only uses ETH equal to `msg.value` of the outermost call, and revert if `address(this).balance > msg.value` at the time of payment.

### Proof of Concept

```solidity
// 1. User A calls exactInputSingle(tokenIn=WETH, amountIn=0.5e18) with msg.value=1e18.
//    pay() deposits 0.5e18 ETH, leaving 0.5e18 ETH on the router.
//    User A does NOT call refundETH().

// 2. Attacker calls exactInputSingle(tokenIn=WETH, amountIn=0.5e18) with msg.value=0.
//    Callback fires: pay(WETH, attacker, pool, 0.5e18).
//    nativeBalance = 0.5e18 >= value = 0.5e18 → branch at line 75 fires.
//    Router deposits 0.5e18 of User A's ETH and transfers WETH to pool.
//    Attacker's transferFrom is never called; attacker pays nothing.

// Assert: router.balance == 0, User A's 0.5e18 ETH is gone, attacker received swap output for free.
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L78-81)
```text
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
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
