The vulnerability is real and confirmed. Here is the full analysis:

---

### Title
Stranded ETH in Router Consumed by Subsequent WETH Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay()` uses the router's entire native ETH balance as a substitute for WETH payment without verifying that the ETH was deposited by the current caller. Any ETH left in the router from a prior user's incomplete multicall (missing `refundETH`) can be silently consumed to fund a subsequent user's WETH swap, with no pull from the attacker's wallet.

### Finding Description

The WETH hybrid branch in `pay()` reads `address(this).balance` and, if it covers the requested `value`, wraps and forwards that ETH to the pool — pulling nothing from `payer`: [1](#0-0) 

The `payer` stored in transient context is always `msg.sender` of the swap entry-point: [2](#0-1) 

And `_justPayCallback` passes that payer directly to `pay()`: [3](#0-2) 

There is no accounting of *which* user deposited the ETH currently sitting in `address(this).balance`. The balance is a shared, unattributed pool.

### Impact Explanation

**Direct loss of user principal.** User A's ETH is irreversibly consumed to settle User B's swap obligation. User A receives nothing in return; User B receives the full swap output while paying zero WETH from their wallet. The loss equals the stranded ETH amount, which can be up to the full `msg.value` of User A's multicall if `refundETH` was omitted.

### Likelihood Explanation

The precondition — ETH stranded in the router — is realistic:
- Users routinely omit `refundETH` from multicalls, especially when they believe the swap will consume the exact amount sent.
- Slippage or partial fills leave residual ETH.
- `refundETH` itself sends to `msg.sender`, not the original depositor, so a well-meaning third party calling it would steal the ETH rather than return it. [4](#0-3) 

An attacker can monitor the mempool for multicalls that omit `refundETH` and front-run or immediately follow with a WETH swap sized to exactly the stranded amount.

### Recommendation

Track per-call deposited ETH using transient storage. At the start of each top-level call (or multicall entry), record `msg.value` as the "authorized native balance" for this call. In `pay()`, deduct only from that authorized amount rather than from `address(this).balance` globally. Revert if the authorized balance is insufficient. Clear the authorized balance at the end of the call. Alternatively, enforce that `address(this).balance` is zero at the start of every non-multicall entry point, and that multicall tracks and enforces per-call ETH budgets.

### Proof of Concept

```
// Step 1: User A sends 1 ETH, swaps 0.9 ETH worth of WETH, omits refundETH
router.multicall{value: 1 ether}([
    abi.encodeCall(router.exactInputSingle, (ExactInputSingleParams({
        tokenIn: WETH, tokenOut: token1, amountIn: 0.9 ether, ...
    })))
    // no refundETH call
]);
// router.balance == 0.1 ETH (User A's stranded ETH)

// Step 2: Attacker (User B) calls exactInputSingle for WETH, sends 0 ETH
// amountIn = 0.1 ether, tokenIn = WETH
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    tokenIn: WETH, tokenOut: token1, amountIn: 0.1 ether, ...
}));
// Inside pay(WETH, UserB, pool, 0.1 ether):
//   nativeBalance = 0.1 ETH >= value = 0.1 ETH
//   → deposit{value: 0.1 ETH}() + safeTransfer(pool, 0.1 WETH)
//   → UserB pulls 0 WETH from wallet

// Result:
assert(address(router).balance == 0);   // User A's 0.1 ETH is gone
assert(weth.balanceOf(UserB_wallet) == weth.balanceOf(UserB_wallet_before)); // UserB paid nothing
// UserB received full swap output; UserA lost 0.1 ETH
```

The invariant "each user's ETH is consumed only for their own swap" is broken. The root cause is in `PeripheryPayments.pay()` at the `nativeBalance >= value` branch, which treats the entire contract ETH balance as belonging to the current caller. [5](#0-4)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
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
