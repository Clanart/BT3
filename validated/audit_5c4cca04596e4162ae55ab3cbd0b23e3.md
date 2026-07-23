Audit Report

## Title
Unguarded `refundETH()` Allows Any Caller to Steal ETH Stranded by Direct Payable Swap Calls — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no access control. When a user calls a payable swap function (e.g., `exactOutputSingle`) directly with `msg.value` exceeding the actual swap input, the surplus ETH is left in the router after the transaction. Any subsequent caller can invoke `refundETH()` to steal that stranded ETH. The `receive()` guard does not prevent this because ETH enters via the payable function's call, not via a plain ETH transfer.

## Finding Description

`refundETH()` is unconditional: [1](#0-0) 

The `receive()` guard only blocks plain ETH sends to the contract address: [2](#0-1) 

It does not intercept ETH attached to a `payable` function call. When a user calls `exactOutputSingle{value: X}(...)` directly, `msg.value = X` is deposited into the router via the function's `payable` modifier; the `receive()` fallback is never triggered.

Inside `pay()`, when `tokenIn == WETH` and `nativeBalance >= value`, only exactly `value` wei is wrapped and forwarded to the pool: [3](#0-2) 

The remainder (`msg.value − actualAmountIn`) stays as raw ETH in the router. Neither `exactOutputSingle` nor `exactInputSingle` contain any post-swap ETH refund: [4](#0-3) [5](#0-4) 

The intended safe pattern is `multicall{value}([swap, refundETH()])`, which is atomic. The existing test confirms this pattern works: [6](#0-5) 

However, the swap functions are `external payable` and callable directly. There is no guard preventing the direct-call path that strands ETH, and `refundETH()` has no per-user accounting or caller restriction.

The payments test explicitly confirms `refundETH()` sends the full router balance to any arbitrary caller: [7](#0-6) 

## Impact Explanation

Direct loss of user ETH principal. For `exactOutputSingle`, users routinely send a buffer above the expected input because the exact input is pool-determined at execution time. Any excess ETH (`msg.value − actualAmountIn`) is stranded in the router and can be stolen by any caller of `refundETH()` in a subsequent transaction. The stolen amount equals the buffer, which can be arbitrarily large relative to the actual swap cost.

## Likelihood Explanation

Medium. The documented and tested pattern is `multicall{value}([swap, refundETH()])`. However, the swap functions are `external payable` and callable directly — a natural integration pattern when the exact input is unknown. A mempool-watching attacker can observe a victim's direct `exactOutputSingle{value: buffer}` call, wait for it to confirm, then call `refundETH()` in the next block. No special privileges are required; any EOA or contract can execute the theft.

## Recommendation

Add an automatic ETH refund at the end of each payable swap function, or restrict `refundETH()` to only be callable via `delegatecall` from `multicall` (e.g., check `address(this) == implementation`). The simplest fix is to refund any remaining `address(this).balance` to `msg.sender` at the end of `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`.

## Proof of Concept

```solidity
function test_refundETH_stealsStrandedETH() public {
    address userA = makeAddr("userA");
    address attacker = makeAddr("attacker");
    vm.deal(userA, 2 ether);

    // userA calls exactOutputSingle directly with 2 ETH buffer;
    // actual amountIn is pool-determined (e.g., ~1000 wei).
    vm.prank(userA);
    router.exactOutputSingle{value: 2 ether}(
        IMetricOmmSimpleRouter.ExactOutputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountOut: 1_000,
            amountInMaximum: 2 ether,
            recipient: userA,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // ~2 ETH - actualAmountIn is now stranded in the router.
    assertGt(address(router).balance, 0, "ETH stranded");

    // Attacker steals it in a subsequent transaction.
    uint256 before = attacker.balance;
    vm.prank(attacker);
    router.refundETH();
    assertGt(attacker.balance, before, "attacker stole userA's ETH");
    assertEq(address(router).balance, 0, "router drained");
}
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.payments.t.sol (L74-85)
```text
  function test_refundETH_sendsBalanceToCaller() public {
    uint256 amount = 2 ether;
    vm.deal(address(router), amount);

    uint256 swapperBefore = swapper.balance;

    vm.prank(swapper);
    router.refundETH();

    assertEq(swapper.balance - swapperBefore, amount, "swapper refunded");
    assertEq(address(router).balance, 0, "router eth cleared");
  }
```
