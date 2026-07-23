Audit Report

## Title
Excess native ETH sent to `exactOutputSingle`/`exactOutput` is silently trapped in the router and claimable by any caller via `refundETH()` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
When a user calls `exactOutputSingle` or `exactOutput` with `tokenIn = WETH` and sends more native ETH than the pool consumes, `PeripheryPayments.pay()` wraps only the exact amount owed and leaves the remainder as raw ETH in the router. Neither swap function performs a post-swap refund. Because `refundETH()` is permissionless and sends to `msg.sender`, any third party can drain the trapped ETH in a follow-up call.

## Finding Description
`PeripheryPayments.pay()` handles the WETH-as-native-ETH path by wrapping only `value` wei: [1](#0-0) 

When `nativeBalance >= value`, only `value` is wrapped and forwarded; `nativeBalance − value` remains as raw ETH in the router with no accounting or automatic return.

`exactOutputSingle` performs no post-swap ETH balance check or refund — it calls `_clearExpectedCallbackPool()` and returns: [2](#0-1) 

The only recovery path is `refundETH()`, which is permissionless and sends the full native balance to `msg.sender`, not the original payer: [3](#0-2) 

## Impact Explanation
Direct loss of user principal. A user who calls `exactOutputSingle{value: X}` where `X > amountIn` (the amount the pool actually charges) permanently loses `X − amountIn` ETH unless they wrap the call in a `multicall` with an explicit `refundETH()`. A MEV bot or any observer can back-run the swap transaction with a standalone `refundETH()` call and collect the surplus. This meets the Sherlock threshold for Medium/High direct loss of user funds.

## Likelihood Explanation
Medium-High. For exact-output swaps the caller cannot know `amountIn` in advance; sending `msg.value = amountInMaximum` as a safety buffer is the natural and documented pattern. The `ExactOutputSingleParams` struct explicitly includes `amountInMaximum` as the slippage guard: [4](#0-3) 

The existing test suite covers `multicall + refundETH` only for `exactInputSingle`: [5](#0-4) 

The `test_mixedNativeAndWeth_exactOutputSingle_wethForToken` test sends exactly the pre-quoted native amount (no surplus), so it does not guard the over-sending path: [6](#0-5) 

## Recommendation
At the end of `exactOutputSingle` and `exactOutput`, automatically refund any remaining native ETH balance to `msg.sender`:

```solidity
// after _clearExpectedCallbackPool()
uint256 residual = address(this).balance;
if (residual > 0) _transferETH(msg.sender, residual);
```

Alternatively, enforce that `msg.value` equals the actual `amountIn` returned by the pool, reverting if the user over-sent.

## Proof of Concept
1. User calls `exactOutputSingle{value: 2 ether}(...)` with `tokenIn = WETH`, `amountOut = X`, `amountInMaximum = 2 ether`.
2. Pool determines `amountIn = 1 ether` and triggers `metricOmmSwapCallback`.
3. `_justPayCallback` → `pay(WETH, user, pool, 1 ether)`: wraps 1 ether, sends WETH to pool. [7](#0-6) 
4. `1 ether` remains as native ETH in the router; `exactOutputSingle` returns `amountIn = 1 ether` with no refund. [2](#0-1) 
5. Attacker calls `refundETH()` in a subsequent transaction and receives `1 ether`. [3](#0-2)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L145-147)
```text
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol (L128-139)
```text
  struct ExactOutputSingleParams {
    address pool;
    address tokenIn;
    address tokenOut;
    bool zeroForOne;
    uint128 amountOut;
    uint128 amountInMaximum;
    address recipient;
    uint256 deadline;
    uint128 priceLimitX64;
    bytes extensionData;
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
