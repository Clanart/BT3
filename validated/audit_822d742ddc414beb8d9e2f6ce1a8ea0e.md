Audit Report

## Title
Router `pay()` WETH Branch Silently Consumes Stranded Native ETH, Enabling Free Swaps at Prior Users' Expense — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` helper in `PeripheryPayments.sol` uses the router's entire native-ETH balance to satisfy WETH payment obligations without verifying that the current caller deposited that ETH. Because `exactInputSingle` and `exactInput` are `payable` but do not enforce `msg.value == amountIn` or auto-refund excess ETH, any surplus ETH left on the router from a prior call is silently consumed by the next caller's WETH swap, giving that caller a free trade at the prior user's expense.

## Finding Description
`PeripheryPayments.pay()` (L73–84) handles WETH payments by reading `address(this).balance` and, when `nativeBalance >= value`, wrapping exactly `value` wei from the router's balance and forwarding it to the pool — the `payer` argument is never consulted:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // payer ignored
    } ...
}
``` [1](#0-0) 

`exactInputSingle` is declared `payable` but imposes no constraint that `msg.value` equals `params.amountIn`, and it does not call `refundETH()` before returning: [2](#0-1) 

The `receive()` guard only rejects direct ETH transfers from non-WETH addresses; it does not fire when ETH arrives via a `payable` function call, so excess `msg.value` accumulates silently on the router. [3](#0-2) 

The callback path that reaches `pay()` is:
`exactInputSingle` → `pool.swap()` → `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, payer, pool, value)`. [4](#0-3) 

There is no transient-storage accounting that ties the router's ETH balance to the caller who deposited it. Any ETH remaining from a prior transaction is freely available to the next WETH-input swap.

## Impact Explanation
Direct loss of user principal. A victim's stranded ETH is permanently transferred to a pool as payment for an attacker's trade. The attacker receives the full swap output without spending any ETH or WETH. This satisfies the "Critical/High/Medium direct loss of user principal above Sherlock thresholds" gate.

## Likelihood Explanation
Medium. The precondition — ETH stranded on the router — arises naturally when a user sends `msg.value` larger than `amountIn` (e.g., to avoid a partial-fill revert) or uses `multicall` without a trailing `refundETH` call. Both patterns are common. Once ETH is stranded, any observer can exploit it in the very next block with a zero-cost WETH swap requiring no special privileges.

## Recommendation
**Short term:** In the `nativeBalance >= value` branch, verify that the ETH being consumed was deposited by the current top-level call (e.g., track per-call ETH credit in transient storage alongside the existing payer/token context and deduct from it rather than from the raw contract balance). Alternatively, enforce `msg.value >= value` at the `exactInputSingle`/`exactInput` entry points when `tokenIn == WETH`.

**Long term:** Add an invariant check that `address(this).balance == 0` at the start of every non-payable entry point, or auto-call `refundETH` at the end of every payable swap function to prevent ETH from ever being stranded between transactions.

## Proof of Concept
```
Block N:
  Alice calls exactInputSingle{value: 1000}(tokenIn=WETH, amountIn=500, ...)
  → metricOmmSwapCallback → _justPayCallback → pay(WETH, Alice, pool, 500)
      nativeBalance = 1000 >= 500
      router wraps 500 wei, sends WETH to pool  ← Alice's 500 wei consumed
      500 wei remains on router
  Alice does NOT call refundETH().

Block N+1:
  Bob calls exactInputSingle{value: 0}(tokenIn=WETH, amountIn=500, ...)
  → metricOmmSwapCallback → _justPayCallback → pay(WETH, Bob, pool, 500)
      nativeBalance = 500 >= 500
      router wraps Alice's 500 wei, sends WETH to pool
      Bob receives TOKEN output, pays nothing
```
Alice loses 500 wei. Bob receives a full swap output at zero cost. Reproducible as a Foundry fork test by deploying the router, funding Alice's call with excess ETH, skipping `refundETH`, then executing Bob's zero-value WETH swap and asserting Bob's output token balance increased while the router's ETH balance is zero.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-86)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
      );
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

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
