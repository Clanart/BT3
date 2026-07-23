Audit Report

## Title
Unspent ETH Permanently Stranded in Router After Exact-Output and Price-Limited Exact-Input Swaps — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

## Summary

All four swap entry-points in `MetricOmmSimpleRouter` are `payable` and the inherited `PeripheryPayments.pay()` function consumes native ETH from the router's balance when `tokenIn == WETH`. For exact-output swaps the pool callback requests only the precise `amountIn`, leaving any ETH above that amount stranded on the router. For price-limited `exactInputSingle` swaps the pool may consume less than the full `amountIn`. None of the four functions call `refundETH()` before returning, and `refundETH()` is a public function that transfers the entire ETH balance to `msg.sender`, allowing any third party to drain the surplus.

## Finding Description

`PeripheryPayments.pay()` handles native ETH when `token == WETH`:

```
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // wraps exactly what pool requested
    IERC20(WETH).safeTransfer(recipient, value);
}
```

Only `value` (the pool-requested amount) is wrapped and forwarded; any excess native balance remains on the contract. [1](#0-0) 

`exactOutputSingle` enforces `amountIn <= amountInMaximum` but never refunds the difference: [2](#0-1) 

`exactOutput` similarly ends without a refund: [3](#0-2) 

`exactInputSingle` passes a user-supplied `priceLimitX64` to the pool; if the price limit is hit the pool stops early and the callback pays only the consumed portion, leaving the remainder on the router: [4](#0-3) 

`refundETH()` is public with no access control and sends the entire ETH balance to `msg.sender`: [5](#0-4) 

The intended usage pattern (demonstrated in tests) is to compose `[swap, refundETH]` inside a `multicall`. However, the swap functions themselves provide no automatic refund, so any user who calls a swap function directly (not via multicall) or omits the `refundETH` step loses the surplus permanently until a third party claims it. [6](#0-5) 

## Impact Explanation

Direct loss of user principal (native ETH). A user performing an exact-output WETH swap who sends `amountInMaximum` ETH loses `amountInMaximum − amountIn` wei with no recourse. A mempool-watching bot can back-run the swap transaction with a call to `refundETH()` in the same block, atomically stealing the surplus. No privileged access is required. The loss scales with the difference between the user's slippage budget and the actual execution price, which can be substantial for volatile pools.

## Likelihood Explanation

Exact-output swaps are a standard, documented use-case. Any user who calls `exactOutputSingle` or `exactOutput` directly (not via multicall) with ETH and `amountIn < amountInMaximum` is affected on every such transaction. The attack requires only a public call to `refundETH()` after the victim's transaction, making it trivially automatable by any MEV bot. The same risk applies to `exactInputSingle` whenever a non-open `priceLimitX64` causes a partial fill.

## Recommendation

Add `refundETH()` at the end of each of the four swap functions, after `_clearExpectedCallbackPool()`:

```solidity
// exactInputSingle, exactInput, exactOutputSingle, exactOutput
_clearExpectedCallbackPool();
refundETH();   // returns any unspent native ETH to msg.sender
```

This mirrors the standard Uniswap v3 periphery remediation and the pattern already demonstrated in the project's own test suite (`test_multicall_ethInput_exactInputSingle_refundsUnusedEth`).

## Proof of Concept

1. Alice calls `exactOutputSingle{value: 3000e18}(amountOut=1e18, amountInMaximum=3000e18, tokenIn=WETH, ...)`.
2. The pool's callback requests `amountIn = 2950e18`. `pay()` wraps and forwards `2950e18` wei; `50e18` wei remains on the router.
3. `exactOutputSingle` returns successfully (passes `amountIn <= amountInMaximum`). No refund is issued.
4. Bob calls `router.refundETH()` in the next transaction. The `50e18` wei is transferred to Bob.
5. Alice loses `50e18` wei with no recourse.

Foundry test outline:
```solidity
uint256 aliceBefore = alice.balance;
vm.prank(alice);
router.exactOutputSingle{value: amountInMaximum}(params);
// router holds surplus ETH
vm.prank(bob);
router.refundETH();
assertGt(bob.balance, 0);           // bob stole Alice's surplus
assertEq(alice.balance, aliceBefore - amountInMaximum); // Alice lost full budget
```

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
