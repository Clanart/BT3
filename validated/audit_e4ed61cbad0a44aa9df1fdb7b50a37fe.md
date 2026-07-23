Audit Report

## Title
Excess native ETH sent to `exactOutputSingle` / `exactInputSingle` is permanently stranded on the router and claimable by any caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`exactOutputSingle` and `exactInputSingle` are `payable` functions that accept native ETH as WETH input. When `msg.value` exceeds the actual amount consumed by the pool, `pay()` wraps and forwards only the exact owed amount, leaving the surplus as raw ETH on the router. No post-swap refund exists in either function. The permissionless `refundETH()` function allows any caller to drain the entire router ETH balance to themselves, stealing the stranded surplus from the original user.

## Finding Description

`exactOutputSingle` (line 130) and `exactInputSingle` (line 67) are both `payable`. [1](#0-0) [2](#0-1) 

During the swap callback, `_justPayCallback` calls `pay(tokenIn, payer, pool, amountOwed)`. [3](#0-2) 

Inside `pay()`, when `token == WETH` and `nativeBalance >= value`, only `value` wei is wrapped and forwarded; the remainder `nativeBalance - value` is never returned. [4](#0-3) 

After the pool swap returns, `exactOutputSingle` performs only a slippage check and clears the callback context — no ETH refund step exists. [5](#0-4) 

The permissionless `refundETH()` sends the **entire** router ETH balance to `msg.sender`, not to the original depositor. [6](#0-5) 

The `receive()` guard (only accepts ETH from WETH) does not prevent the surplus from being stranded, because the user's ETH arrives as `msg.value` on the function call itself, not through `receive()`. [7](#0-6) 

## Impact Explanation

Any user who calls `exactOutputSingle{value: amountInMaximum}(...)` with `tokenIn == WETH` and `amountInMaximum > actualAmountIn` permanently loses `amountInMaximum - actualAmountIn` ETH. The stranded ETH is immediately claimable by any third party via `refundETH()`. This is a direct, unconditional loss of user principal with no recovery path. The same applies to `exactInputSingle` if `msg.value` exceeds the fixed `amountIn` (e.g., due to a user error or integrator miscalculation). Severity: **High** — direct loss of user funds, no privilege required for the attacker.

## Likelihood Explanation

The exact-output pattern universally requires sending `amountInMaximum` upfront. Users or integrators who call `exactOutputSingle` directly without wrapping in a `multicall` that appends `refundETH()` — a natural and common pattern — will always lose the surplus. The functions are `payable` and accept ETH without restriction or warning, giving no indication that the caller must manually reclaim change. MEV bots monitoring the mempool can front-run or back-run to capture stranded ETH atomically.

## Recommendation

Add an automatic ETH refund at the end of each `payable` swap entry point after `amountIn` is known:

```solidity
// In exactOutputSingle, after amountIn is determined:
if (params.tokenIn == WETH && msg.value > amountIn) {
    _transferETH(msg.sender, msg.value - amountIn);
}
```

Apply the same pattern to `exactInputSingle`, `exactInput`, and `exactOutput`. Alternatively, embed a `refundETH()` call at the end of each function body so the refund is automatic and callers cannot accidentally strand ETH.

## Proof of Concept

1. Pool is `WETH/TOKEN1`. The swap requires `1000 wei` WETH to buy the desired output.
2. User calls `exactOutputSingle{value: 5000}(params)` with `amountInMaximum = 5000`, `tokenIn = WETH`.
3. Pool triggers `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, user, pool, 1000)`.
4. Inside `pay`: `nativeBalance = 5000 >= value = 1000` → wraps and sends exactly `1000 wei` WETH to pool. Remaining `4000 wei` stays on router. [8](#0-7) 
5. `exactOutputSingle` returns `amountIn = 1000`. No refund is issued. [5](#0-4) 
6. MEV bot calls `refundETH()` in the next block → receives `4000 wei`. User's `4000 wei` is permanently lost. [6](#0-5)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L141-147)
```text
    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```
