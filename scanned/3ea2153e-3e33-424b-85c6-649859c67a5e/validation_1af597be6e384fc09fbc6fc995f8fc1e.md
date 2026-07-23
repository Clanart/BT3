### Title
Excess Native ETH Sent to Payable Swap Functions Is Not Automatically Refunded and Can Be Stolen by Any Caller — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`exactOutputSingle`, `exactOutput`, `exactInputSingle`, and `exactInput` are all `payable` and accept native ETH. When `tokenIn == WETH`, the internal `pay()` function wraps only the exact amount the pool requests, leaving any excess ETH silently stranded in the router. Because `refundETH()` sends the entire router balance to `msg.sender` (not to the original depositor), any third party can call it in the same block and steal the leftover ETH.

---

### Finding Description

`pay()` in `PeripheryPayments` handles native ETH by comparing `address(this).balance` against the required `value`: [1](#0-0) 

When `nativeBalance >= value`, it deposits exactly `value` ETH into WETH and forwards it to the pool. The remainder (`nativeBalance - value`) is never touched and stays in the router for the rest of the transaction.

`exactOutputSingle` is the most dangerous entry point: the caller cannot know the exact `amountIn` the pool will charge before the swap executes, so a rational user sends `amountInMaximum` as ETH. The pool charges `actualAmountIn ≤ amountInMaximum`; the difference is excess. [2](#0-1) 

After `exactOutputSingle` returns, the excess ETH sits in the router. `refundETH()` sends the full router balance to whoever calls it: [3](#0-2) 

There is no access control on `refundETH()` and no binding between the original depositor and the refund recipient. A MEV bot observing the mempool can front-run or back-run the victim's swap and call `refundETH()` to claim the stranded ETH.

The same issue applies to `exactOutput` (multihop exact-output) and, to a lesser degree, `exactInputSingle` / `exactInput` when a user sends more ETH than `amountIn`. [4](#0-3) 

The existing test suite acknowledges the pattern but only validates the safe path (multicall + explicit `refundETH()`): [5](#0-4) 

No test covers the case where a user calls `exactOutputSingle` directly with `msg.value > actualAmountIn` without a subsequent `refundETH()` in the same multicall.

---

### Impact Explanation

Any ETH sent above the pool-determined `amountIn` is permanently stranded in the router after the swap call returns. Because `refundETH()` is permissionless and sends to `msg.sender`, a third party (MEV bot) can steal the entire stranded balance in the same block. This is a direct, unconditional loss of user principal with no recovery path once the attacker claims it.

---

### Likelihood Explanation

Exact-output swaps are the primary use case where callers cannot pre-compute the precise input amount. Sending `amountInMaximum` as ETH is the natural usage pattern. MEV infrastructure routinely monitors for stranded ETH in router contracts. The attack requires no special privilege, no malicious setup, and no non-standard token behavior — only a standard `exactOutputSingle` call with `tokenIn == WETH` and `msg.value > actualAmountIn`.

---

### Recommendation

Automatically refund any remaining native ETH balance to `msg.sender` at the end of each payable swap function, before returning:

```solidity
function exactOutputSingle(ExactOutputSingleParams calldata params)
    external payable returns (uint256 amountIn)
{
    // ... existing swap logic ...
    _clearExpectedCallbackPool();

    // Refund excess ETH to the caller
    uint256 remaining = address(this).balance;
    if (remaining > 0) _transferETH(msg.sender, remaining);
}
```

Apply the same pattern to `exactOutput`, `exactInputSingle`, and `exactInput`. Alternatively, enforce that `msg.value` equals exactly the amount to be used (rejecting overpayment), though this is less ergonomic for exact-output callers.

---

### Proof of Concept

```solidity
// Attacker steals Alice's excess ETH from an exactOutputSingle call

// 1. Alice calls exactOutputSingle with msg.value = amountInMaximum = 2 ETH
//    Pool charges actualAmountIn = 1.2 ETH
//    0.8 ETH excess remains in router

// 2. Attacker (Bob) observes Alice's tx in mempool, submits refundETH() 
//    immediately after (or in same block)
router.refundETH(); // Bob receives Alice's 0.8 ETH

// Concrete assertion:
uint256 aliceEthBefore = alice.balance;
vm.prank(alice);
router.exactOutputSingle{value: 2 ether}(
    IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: amountOut,           // results in actualAmountIn = 1.2 ETH
        amountInMaximum: 2 ether,
        recipient: alice,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Router now holds 0.8 ETH

uint256 bobEthBefore = bob.balance;
vm.prank(bob);
router.refundETH();
assertEq(bob.balance - bobEthBefore, 0.8 ether); // Bob stole Alice's excess
assertEq(alice.balance, aliceEthBefore - 2 ether); // Alice lost full 2 ETH
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
