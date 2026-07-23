The core question is whether ETH can persist in the router between transactions, making the unguarded `refundETH()` exploitable. Let me trace the exact payment flow.

The payment flow is now fully traced. Here is the verdict:

---

### Title
Unguarded `refundETH()` allows any caller to steal ETH stranded in the router after a direct payable swap call — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`refundETH()` has no access control and sends the router's entire ETH balance to `msg.sender`. ETH can be permanently stranded in the router between transactions when a user calls `exactInputSingle` (or any other payable swap function) directly with `msg.value > amountIn`. An attacker can then call `refundETH()` in a subsequent transaction to steal the victim's residual ETH.

### Finding Description

`refundETH()` unconditionally transfers all native ETH held by the router to `msg.sender`: [1](#0-0) 

There is no check that `msg.sender` is the original depositor, no reentrancy guard, and no per-caller accounting.

ETH enters the router via `msg.value` on any `payable` swap function (e.g. `exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`). The `receive()` guard only blocks plain ETH transfers; it does not restrict `msg.value` on calls with calldata: [2](#0-1) 

During the swap callback, `pay()` consumes **exactly** `amountIn` worth of native ETH by wrapping it into WETH — but only when `nativeBalance >= value`: [3](#0-2) 

If `msg.value > amountIn`, the surplus ETH (`msg.value - amountIn`) is **not refunded** at the end of `exactInputSingle`: [4](#0-3) 

The function simply returns after clearing the callback context. No automatic sweep or refund occurs. The surplus ETH sits in the router until the next `refundETH()` caller — who need not be the victim.

The same stranding applies to `exactOutputSingle` with ETH input: the user cannot know the exact `amountIn` before the transaction, so they must send excess ETH, which is then stranded. [5](#0-4) 

### Impact Explanation

Direct loss of user ETH principal. Any ETH sent as `msg.value` in excess of the swap's actual cost is permanently accessible to any caller of `refundETH()`. The attacker receives the victim's ETH with no preconditions. This meets the contest's High threshold for direct loss of user principal.

### Likelihood Explanation

- `exactOutputSingle` with ETH input is the most common trigger: the user cannot know the exact cost upfront and must overpay.
- `exactInputSingle` called directly (not via `multicall`) with `msg.value > amountIn` also strands ETH.
- The attack requires no privileged access, no malicious pool, and no special token behavior.
- An attacker can monitor the mempool for transactions that leave ETH in the router and immediately call `refundETH()` in the next block.

### Recommendation

Either:
1. At the end of each payable swap function, automatically refund any remaining `address(this).balance` to `msg.sender`, or
2. Add a `msg.sender`-binding guard to `refundETH()` by recording the original caller in transient storage at swap entry and only allowing that address to call `refundETH()` within the same multicall context.

The simplest fix is to add an auto-refund at the tail of each payable entry point (matching the pattern already used in `multicall` + `refundETH`).

### Proof of Concept

```solidity
// Victim calls exactOutputSingle with excess ETH (cannot know exact cost upfront)
uint256 amountIn = router.exactOutputSingle{value: 1.001 ether}(
    IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: someAmount,          // costs exactly 1 ETH
        amountInMaximum: 1.001 ether,
        recipient: victim,
        deadline: block.timestamp + 60,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// amountIn == 1 ETH; 0.001 ETH remains in router

// Attacker (separate tx, next block)
uint256 attackerBefore = attacker.balance;
vm.prank(attacker);
router.refundETH();
assertEq(attacker.balance - attackerBefore, 0.001 ether); // attacker stole victim's ETH
```

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
