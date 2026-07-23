The `pay()` function in `PeripheryPayments.sol` is the critical code to analyze. Let me read it carefully alongside the `receive()` guard and the test that validates the mixed-ETH flow.

The vulnerability claim is real and traceable through the code. Here is the full analysis:

---

### Title
Residual ETH on Router Consumed by Subsequent User's WETH Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`pay()` uses `address(this).balance` as an unaccounted shared pool for WETH wrapping. ETH left on the router from a prior payable call (where the user overpaid and did not call `refundETH()`) is silently consumed by the next user's WETH swap, causing the prior user to lose their ETH and the subsequent user to underpay.

### Finding Description

The `receive()` guard in `PeripheryPayments.sol` only blocks plain ETH transfers (no calldata): [1](#0-0) 

However, all swap entry points (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`) are `payable`, so ETH sent with those calls is accepted without restriction and accumulates in `address(this).balance`. If a user sends more ETH than `amountIn` and omits `refundETH()`, the surplus persists across transaction boundaries. [2](#0-1) 

When the next user calls any WETH-input swap, `pay()` reads the unguarded `address(this).balance`: [3](#0-2) 

The `else if (nativeBalance > 0)` branch wraps the entire residual ETH balance, transfers it to the pool, then pulls only `value - nativeBalance` WETH from the current payer. There is no per-user accounting, no check that the ETH was sent in the current transaction, and no guard preventing cross-transaction ETH reuse.

### Impact Explanation

- **User A** sends 1 ETH with `exactInputSingle{value: 1 ETH}(amountIn=0.5 ETH, tokenIn=WETH)` and omits `refundETH()`. After the swap, 0.5 ETH remains on the router.
- **User B** calls `exactInputSingle(amountIn=1 ETH, tokenIn=WETH)` (no ETH sent). `pay()` sees `nativeBalance=0.5 ETH`, wraps it, sends to pool, then pulls only 0.5 WETH from user B's wallet.
- **Result**: User A loses 0.5 ETH permanently. User B pays 0.5 WETH instead of 1 WETH. The pool receives the correct 1 WETH total, so pool solvency is unaffected, but user A suffers a direct principal loss.

The test suite explicitly demonstrates the residual-ETH scenario (sending 2 ETH for a 1000-unit swap) and relies on `refundETH()` to recover the surplus — confirming the router holds ETH between calls when `refundETH()` is absent: [4](#0-3) 

### Likelihood Explanation

- Any user who sends a round ETH amount (e.g., 1 ETH) for a swap that costs less, or uses a frontend that does not append `refundETH()` to the multicall, leaves ETH on the router.
- An attacker can monitor the mempool for such transactions and front-run or immediately follow with a WETH swap to consume the residual ETH.
- No privileged access, malicious pool, or non-standard token is required.

### Recommendation

Track the ETH that belongs to the current call context. The simplest fix is to record `msg.value` at entry and use only that amount in `pay()`, rather than the full `address(this).balance`. Alternatively, enforce that `pay()` only uses ETH up to `msg.value` of the outermost call, and revert if `address(this).balance` exceeds `msg.value` at the end of each top-level entry point (similar to Uniswap v3's `checkDeadline` + balance-diff pattern).

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_residualEthConsumedByNextUser() public {
    uint128 amountIn = 1 ether;
    uint256 nativeSent = 1 ether;
    uint256 actualCost = 0.5 ether; // pool only needs 0.5 ETH worth

    // User A: sends 1 ETH, swap costs 0.5 ETH, forgets refundETH()
    vm.prank(userA);
    router.exactInputSingle{value: nativeSent}(
        ExactInputSingleParams({tokenIn: WETH, amountIn: actualCost, ...})
    );
    // 0.5 ETH remains on router

    uint256 userBWethBefore = weth.balanceOf(userB);

    // User B: no ETH sent, expects to pay 1 WETH from wallet
    vm.prank(userB);
    router.exactInputSingle(
        ExactInputSingleParams({tokenIn: WETH, amountIn: 1 ether, ...})
    );

    // User B only pulled 0.5 WETH from their wallet (not 1 WETH)
    assertEq(userBWethBefore - weth.balanceOf(userB), 0.5 ether, "userB underpaid");
    // User A's 0.5 ETH is gone
    assertEq(address(router).balance, 0);
}
``` [5](#0-4)

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
