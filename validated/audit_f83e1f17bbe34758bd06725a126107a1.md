### Title
Router `pay()` WETH Path Silently Consumes Any Stranded Native ETH, Enabling Any Caller to Swap for Free — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments.sol` contains a WETH payment branch that unconditionally uses the router's entire native-ETH balance before pulling from the designated `payer`. Because the router never tracks *which* caller deposited that ETH, any ETH left on the router from a prior transaction (e.g., a user who sent excess `msg.value` without calling `refundETH`) is silently consumed by the next caller's WETH swap — giving that caller a free trade at the prior user's expense.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH payments as follows: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);   // ← payer is never touched
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

When `nativeBalance >= value`, the function wraps the router's own ETH and forwards it to the pool — the `payer` argument is completely ignored. There is no transient-storage accounting that ties the router's ETH balance to the caller who deposited it. Any ETH that remains on the router after a previous transaction (because the prior user sent excess `msg.value` and omitted `refundETH`) is therefore freely available to the next caller.

This is the direct structural analog of the `BondEscalationAccounting` `NoResolution` bug: just as that contract assigns `_numberOfPledges = 1` to *any* address regardless of whether it ever pledged, `pay()` assigns the router's full native-ETH balance to *any* caller's WETH swap regardless of who deposited it.

The `pay()` function is called from `_justPayCallback()`, which is invoked during `metricOmmSwapCallback()` for every `exactInputSingle` and `exactInput` WETH swap: [2](#0-1) 

The callback is reached via `exactInputSingle`: [3](#0-2) 

---

### Impact Explanation

**Direct loss of user principal.** Any ETH stranded on the router is consumed by the next WETH-input swap, regardless of who sent it. The attacker receives the full swap output without spending any ETH or WETH of their own. The victim's ETH is permanently transferred to the pool as payment for the attacker's trade.

---

### Likelihood Explanation

**Medium.** The precondition — ETH left on the router — arises naturally whenever a user:
- sends `msg.value` larger than `amountIn` (e.g., to avoid a revert if the pool fills partially), or
- calls `exactInputSingle` with WETH in a `multicall` that omits the trailing `refundETH` step.

Both patterns are common in practice. Once ETH is stranded, any observer can exploit it in the very next block with a zero-cost WETH swap.

---

### Recommendation

**Short term:** In the `nativeBalance >= value` branch, verify that `msg.value` for the current call covers at least `value`, or store the per-call ETH credit in transient storage (alongside the existing payer/token context) and deduct from it rather than from the raw contract balance.

**Long term:** Enforce an invariant that `address(this).balance == 0` at the start of every non-payable entry point, or emit an event / revert when the router's ETH balance is non-zero at callback time and `msg.value` for the current top-level call is zero.

---

### Proof of Concept

```
Block N:
  Alice calls exactInputSingle{value: 1000}(
      pool=WETH/TOKEN1, tokenIn=WETH, amountIn=500, recipient=Alice, ...
  )
  → pool.swap() fires metricOmmSwapCallback
  → _justPayCallback → pay(WETH, Alice, pool, 500)
      nativeBalance = 1000 >= 500
      router wraps 500 wei, sends WETH to pool  ← Alice's 500 wei used
      500 wei remains on router
  Alice does NOT call refundETH.

Block N+1:
  Bob calls exactInputSingle{value: 0}(
      pool=WETH/TOKEN1, tokenIn=WETH, amountIn=500, recipient=Bob, ...
  )
  → pool.swap() fires metricOmmSwapCallback
  → _justPayCallback → pay(WETH, Bob, pool, 500)
      nativeBalance = 500 >= 500
      router wraps 500 wei, sends WETH to pool  ← Alice's remaining 500 wei used
      Bob receives TOKEN1 output, pays nothing
```

Alice loses 500 wei. Bob receives a full swap output at zero cost.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-87)
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
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
