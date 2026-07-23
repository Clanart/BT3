### Title
Stranded Native ETH on Router Subsidizes Any Subsequent WETH Swap Caller — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

The `pay` function in `PeripheryPayments` uses the router's entire native ETH balance to settle WETH swap obligations without verifying that the ETH was sent by the current payer in the current transaction. Any ETH left on the router from a prior call — most commonly from an `exactOutputSingle` or `exactInputSingle` call where the user sent more ETH than the swap consumed — can be silently consumed by any subsequent caller who sends `msg.value = 0` and specifies WETH as `tokenIn`. The victim loses their stranded ETH; the attacker receives a fully subsidized swap output.

### Finding Description

`PeripheryPayments.pay` contains the following WETH branch:

```solidity
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
}
``` [1](#0-0) 

When `nativeBalance >= value`, the function deposits `value` ETH from the router's balance and transfers the resulting WETH to the pool — **without pulling anything from `payer`**. When `0 < nativeBalance < value`, it uses the router's entire native balance first and only pulls the remainder from `payer`. In neither case is there any check that the native ETH on the router was contributed by the current `payer` in the current transaction.

**How ETH accumulates on the router:**

`exactOutputSingle` is the primary source. The user sends `msg.value = amountInMaximum` because the exact input is unknown before execution. The pool determines the actual `amountIn < amountInMaximum`; the `pay` callback deposits only `amountIn` ETH, leaving `amountInMaximum − amountIn` ETH on the router. Unless the user wraps the call in a `multicall` that also calls `refundETH()`, that ETH is permanently stranded until someone else claims it. [2](#0-1) 

`exactInputSingle` with `msg.value > amountIn` produces the same residue. [3](#0-2) 

**How the attacker drains it:**

Because `exactInputSingle` is `payable` and imposes no `msg.value >= amountIn` check, an attacker calls it with `msg.value = 0`, `tokenIn = WETH`, and `amountIn = <stranded amount>`. The swap callback fires, `pay` sees `nativeBalance >= value`, wraps the victim's ETH into WETH, and forwards it to the pool. The attacker receives the full swap output at zero cost. [4](#0-3) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) prevents arbitrary ETH injection but does not prevent the attack: the ETH was legitimately deposited by the victim via `msg.value`. [5](#0-4) 

### Impact Explanation

Direct loss of user principal. The victim loses the entire ETH surplus they sent (e.g., `amountInMaximum − amountIn` for an exact-output swap). The attacker receives a fully subsidized swap output. The loss is bounded only by the victim's `amountInMaximum` and is realized in a single subsequent transaction with no special privileges required.

### Likelihood Explanation

Medium-High. `exactOutputSingle` with a native ETH input is a standard UX pattern (user sends a safe upper bound and expects change back). Any user who omits `refundETH()` from their multicall — or calls `exactOutputSingle` directly without a multicall — creates the residue. An attacker can watch the mempool for such transactions and immediately follow with a zero-cost `exactInputSingle`. The attack requires no privileged role, no special token, and no pool manipulation.

### Recommendation

Track the ETH contributed by the current payer in the transient callback context. Store `msg.value` alongside the payer address when setting the callback context in `exactInputSingle` / `exactOutputSingle`, and cap the native ETH used in `pay` to that stored value rather than the router's full `

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
  }
```
