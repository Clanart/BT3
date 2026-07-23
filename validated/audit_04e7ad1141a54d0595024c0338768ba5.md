Audit Report

## Title
Unaccounted Router ETH Balance Silently Subsidizes Subsequent WETH Swaps, Stealing Prior User's Refund — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay` reads `address(this).balance` with no per-transaction attribution when `token == WETH`. ETH stranded on the router from a prior user's `msg.value` overpayment is silently consumed to fund a subsequent caller's WETH swap, causing direct loss of the prior user's principal with no privileged access required.

## Finding Description

`exactInputSingle` is `payable` and sets the callback payer to `msg.sender` via `_setNextCallbackContext`. [1](#0-0) 

When the pool callback fires, `_justPayCallback` calls `pay(WETH, payer, pool, value)`. [2](#0-1) 

Inside `pay`, the WETH branch reads the global `address(this).balance` with no accounting for which transaction's `msg.value` it belongs to: [3](#0-2) 

`exactInputSingle` has no automatic `refundETH()` at exit. A user who calls it standalone with `msg.value > amountIn` leaves `msg.value - amountIn` ETH stranded on the router. A subsequent attacker calls `exactInputSingle(tokenIn=WETH, amountIn=Z, msg.value=0)`; the callback invokes `pay(WETH, attacker, pool, Z)`, and the branch at line 75 (`nativeBalance >= value`) fires, depositing the prior user's ETH and transferring WETH to the pool — the attacker's `safeTransferFrom` is never reached.

The `receive()` guard is irrelevant here: it only blocks plain ETH transfers from non-WETH addresses, but `msg.value` attached to a `payable` function call bypasses `receive()` entirely. [4](#0-3) 

## Impact Explanation

Direct loss of user principal. The prior user's stranded ETH (`msg.value - amountIn`) is consumed to fund an unrelated swap. The prior user receives neither WETH nor an ETH refund for that amount. The attacker receives a fully or partially subsidized swap output. This satisfies the Critical/High direct-loss-of-user-principal gate.

## Likelihood Explanation

Moderate. The precondition — ETH stranded on the router — arises naturally whenever a user calls `exactInputSingle` or `exactInput` with `msg.value > amountIn` without bundling `refundETH()` in the same `multicall`. An attacker can monitor the router's ETH balance on-chain and immediately follow with a zero-value WETH swap to drain it. No privileged access, malicious pool, or non-standard token is required.

## Recommendation

Track the ETH belonging to the current transaction separately from any pre-existing router balance. The simplest fix is to record `address(this).balance - msg.value` at entry and restrict `pay` to spending only up to `msg.value` of ETH (i.e., the amount the current caller explicitly sent). Alternatively, enforce that `pay` only uses ETH equal to `msg.value` of the outermost call and revert if `address(this).balance > msg.value` at the time of payment. A complementary measure is to auto-refund excess ETH at the end of `exactInputSingle` and `exactInput`.

## Proof of Concept

```solidity
// 1. User A calls exactInputSingle(tokenIn=WETH, amountIn=0.5e18) with msg.value=1e18.
//    pay() at line 75-77 deposits 0.5e18 ETH → router.balance = 0.5e18 (stranded).
//    User A does NOT call refundETH().

// 2. Attacker calls exactInputSingle(tokenIn=WETH, amountIn=0.5e18) with msg.value=0.
//    Callback fires: pay(WETH, attacker, pool, 0.5e18).
//    nativeBalance = address(this).balance = 0.5e18 >= value = 0.5e18.
//    Line 75 branch fires: router deposits 0.5e18 of User A's ETH, transfers WETH to pool.
//    safeTransferFrom(attacker, ...) is never called.

// Assert: router.balance == 0, User A's 0.5e18 ETH is gone, attacker received swap output for free.
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-71)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-84)
```text
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
```
