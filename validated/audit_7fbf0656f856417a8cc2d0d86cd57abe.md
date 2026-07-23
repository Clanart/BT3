Looking at the actual code to validate each claim:

Audit Report

## Title
Unguarded `refundETH()` allows any caller to steal excess ETH stranded on the router — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`refundETH()` is an `external payable` function with no access control that unconditionally transfers the router's entire ETH balance to `msg.sender`. Because `pay()` deposits only the exact `amountIn` when native ETH is used as WETH input, any `msg.value` exceeding `amountIn` in a direct swap call (without a multicall-wrapped `refundETH`) is permanently stranded on the router and immediately claimable by any subsequent caller.

## Finding Description

`refundETH()` contains no ownership or caller check: [1](#0-0) 

It sends `address(this).balance` to `msg.sender` with no verification that the caller deposited the ETH.

When a user swaps with native ETH as WETH input, `pay()` deposits only the exact `value` (i.e., `params.amountIn`): [2](#0-1) 

Any `msg.value` above `amountIn` remains on the router. The `receive()` guard only blocks plain ETH transfers (no calldata) from non-WETH addresses: [3](#0-2) 

It does **not** prevent ETH from being attached to `payable` function calls such as `exactInputSingle{value: X}(...)` directly. `exactInputSingle` is declared `external payable` and imposes no constraint that `msg.value == params.amountIn`: [4](#0-3) 

The intended safe pattern is to include `refundETH` as the last call in the same atomic `multicall`: [5](#0-4) 

But if a user calls `exactInputSingle{value: excess}` directly (without a multicall wrapper), the excess ETH is stranded on the router with no attribution and is immediately claimable by anyone.

## Impact Explanation

Direct theft of user ETH. Any ETH stranded on the router from excess `msg.value` in a swap that consumed less than the full amount can be drained by an attacker calling `refundETH()` in a subsequent transaction. There is no minimum threshold — the attacker receives the full stranded balance. This constitutes a Critical-severity direct loss of user principal.

## Likelihood Explanation

The pattern of sending excess ETH and relying on `refundETH` in the same multicall is the documented and tested usage pattern. Users calling `exactInputSingle{value}` directly (without multicall) or forgetting to append `refundETH` will strand ETH. MEV bots monitoring mempool or block state can trivially detect a non-zero router ETH balance and call `refundETH()` atomically in the next block. The test `test_refundETH_sendsBalanceToCaller` directly confirms that any arbitrary caller receives the full router balance: [6](#0-5) 

## Recommendation

Restrict `refundETH()` so it can only be called within a `multicall` context (i.e., via `delegatecall` from `multicall`), or record the original `msg.sender` of the outermost `multicall` in transient storage and require `msg.sender == storedCaller` inside `refundETH`. Alternatively, accept a `recipient` parameter validated against the transient payer context, mirroring how `unwrapWETH9` accepts a `recipient` but is called within the same atomic multicall.

## Proof of Concept

```
1. User calls router.exactInputSingle{value: 1 ether}(
       ExactInputSingleParams({
           tokenIn: WETH, amountIn: 0.5 ether, ...
       })
   );
   // pay() deposits exactly 0.5 ETH as WETH → pool; 0.5 ETH remains on router

2. Attacker (separate tx) calls router.refundETH();
   // balance = 0.5 ETH → _transferETH(attacker, 0.5 ETH)
   // Attacker receives 0.5 ETH; user's excess is gone.
```

The existing test `test_refundETH_sendsBalanceToCaller` directly confirms this behavior — it pre-loads the router with ETH via `vm.deal` and shows any caller receives it. [6](#0-5)

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
