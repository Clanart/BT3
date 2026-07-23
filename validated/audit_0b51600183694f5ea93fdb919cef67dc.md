Audit Report

## Title
Excess Native ETH Sent to `exactOutputSingle` / `exactOutput` Is Not Refunded and Is Stealable by Any Caller — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

## Summary

`exactOutputSingle` and `exactOutput` are `payable` and accept native ETH as WETH input. When a caller sends more ETH than the actual `amountIn` consumed by the pool, `pay()` wraps only the exact amount owed and leaves the surplus in the router. Because `refundETH()` unconditionally sends `address(this).balance` to `msg.sender`, any third party can immediately drain the surplus, causing direct, irreversible loss of the original caller's ETH.

## Finding Description

**`pay()` wraps only the exact amount owed, leaving surplus ETH in the router.** [1](#0-0) 

When `token == WETH` and `nativeBalance >= value`, the function deposits exactly `value` ETH into WETH and transfers it to the pool. Any `nativeBalance - value` surplus is silently retained in the router with no refund path.

**`exactOutputSingle` never refunds excess ETH.** [2](#0-1) 

The function is `payable`, accepts arbitrary ETH, determines the actual `amountIn` only after the pool swap executes, checks `amountIn <= amountInMaximum`, then returns — without refunding `msg.value - amountIn`. The same omission exists in `exactOutput`. [3](#0-2) 

**`refundETH()` sends to `msg.sender`, not the original depositor.** [4](#0-3) 

Any address that calls `refundETH()` after the victim's transaction receives the full router ETH balance.

**The `receive()` guard does not prevent ETH from entering via `exactOutputSingle`.** [5](#0-4) 

`receive()` only blocks plain ETH transfers (no calldata). ETH sent as part of a `payable` function call (e.g., `exactOutputSingle{value: 2 ether}(...)`) bypasses this guard entirely and is accepted by the contract.

**The test suite demonstrates the correct multicall+refundETH pattern but does not enforce it at the contract level, and the `exactOutputSingle` native ETH test sends exactly the quoted amount — it never tests the case where `msg.value > actual amountIn`.** [6](#0-5) 

## Impact Explanation

A user calling `exactOutputSingle{value: amountInMaximum}(...)` directly (without multicall) loses `msg.value - actual amountIn` ETH permanently to the first address that calls `refundETH()`. This is a direct, irreversible loss of user principal with no protocol-level guard preventing it. The impact qualifies as High: direct loss of user funds, reachable by any unprivileged caller, with no recovery mechanism.

## Likelihood Explanation

`exactOutputSingle` is `payable` and its interface accepts ETH. Users performing exact-output swaps with native ETH have no way to know the exact `amountIn` before execution; sending `amountInMaximum` is the natural defensive choice. The contract provides no warning, no auto-refund, and no revert if excess ETH is sent. The multicall+refundETH pattern is a convention, not an enforcement. Any MEV bot or frontrunner monitoring the mempool can call `refundETH()` in the same block to steal the surplus.

## Recommendation

Add an automatic ETH refund at the end of `exactOutputSingle` and `exactOutput` when `tokenIn == WETH`:

```solidity
// After amountIn is known:
uint256 surplus = address(this).balance;
if (surplus > 0) _transferETH(msg.sender, surplus);
```

Alternatively, enforce that callers must use multicall by checking `msg.value == 0` when not in a multicall context, or document the requirement prominently in the NatSpec.

## Proof of Concept

1. Alice calls `router.exactOutputSingle{value: 2 ether}(ExactOutputSingleParams({tokenIn: WETH, amountOut: X, amountInMaximum: 2 ether, ...}))`.
2. The pool requires only `1 ether` as `amountIn`. `pay()` wraps exactly `1 ether` and sends it to the pool. `1 ether` remains in the router.
3. Bob (any address) calls `router.refundETH()` in the same block.
4. `refundETH()` sends `address(this).balance` (`1 ether`) to Bob.
5. Alice receives her output tokens but loses `1 ether` of surplus ETH to Bob.

A Foundry test can reproduce this by: (a) calling `exactOutputSingle{value: quotedIn * 2}(...)`, (b) pranking a second address to call `refundETH()`, and (c) asserting that the second address received the surplus and Alice did not.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-188)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = _getExactOutputAmountIn();
    _clearExpectedCallbackPool();
  }
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L72-104)
```text
  function test_mixedNativeAndWeth_exactOutputSingle_wethForToken() public {
    uint128 amountOut = 1_500;
    (uint256 quotedIn,) =
      quoter.quoteHypotheticalExactOutputSingle(address(pool), true, amountOut, 0, TEST_BID_X64, TEST_ASK_X64);
    uint256 nativePart = quotedIn / 2;
    uint256 wethPart = quotedIn - nativePart;

    uint256 token1Before = token1.balanceOf(recipient);
    uint256 swapperEthBefore = swapper.balance;
    uint256 swapperWethBefore = weth.balanceOf(swapper);

    vm.prank(swapper);
    uint256 amountIn = router.exactOutputSingle{value: nativePart}(
      IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: amountOut,
        amountInMaximum: uint128(quotedIn * 2 + 1),
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );

    assertEq(amountIn, quotedIn, "amountIn matches quote");
    assertEq(token1.balanceOf(recipient) - token1Before, amountOut, "exact token1 out");
    assertEq(swapperEthBefore - swapper.balance, nativePart, "swapper native spent");
    assertEq(swapperWethBefore - weth.balanceOf(swapper), wethPart, "swapper weth spent");
    _assertRouterEmpty();
  }
```
