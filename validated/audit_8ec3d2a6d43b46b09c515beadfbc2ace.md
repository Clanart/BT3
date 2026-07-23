Audit Report

## Title
Excess ETH Sent to Payable Swap Entry Points Is Permanently Stranded and Claimable by Any Caller via `refundETH` — (`metric-periphery/contracts/base/PeripheryPayments.sol`, `metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

## Summary

All four payable swap entry points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) accept arbitrary `msg.value` but never auto-refund excess ETH. When `tokenIn == WETH` and `msg.value > amountIn`, `pay()` wraps exactly `amountIn` ETH and leaves the remainder in the router. Because `refundETH()` is permissionless and sends `address(this).balance` to `msg.sender`, any subsequent caller can drain the stranded ETH, causing direct, irreversible loss of user principal.

## Finding Description

**Step 1 — ETH enters the router via a payable function call.**

`exactInputSingle` is declared `payable` and stores `msg.sender` as the payer in transient storage. It does not record `msg.value` or enforce `msg.value == params.amountIn`. [1](#0-0) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks plain ETH transfers; it is not invoked when ETH arrives via a `payable` function call. [2](#0-1) 

**Step 2 — `pay()` wraps exactly `value` ETH, leaving the surplus.**

When `token == WETH` and `nativeBalance >= value`, the WETH branch wraps exactly `value` wei and transfers WETH to the pool. The surplus `nativeBalance - value` ETH is never touched and remains in the router. [3](#0-2) 

**Step 3 — `refundETH()` is permissionless and sends to `msg.sender`.**

`refundETH` transfers the entire native balance of the router to whoever calls it — not to the original depositor. [4](#0-3) 

**Step 4 — No auto-refund exists in any swap entry point.**

All four swap functions end with `_clearExpectedCallbackPool()` and return — no ETH refund is issued. [5](#0-4) 

**Step 5 — The codebase's own test documents the required safe pattern.**

The test file explicitly documents that the intended safe pattern is `multicall{value}([exactInputSingle, refundETH])`, and a dedicated test (`test_multicall_ethInput_exactInputSingle_refundsUnusedEth`) confirms that without `refundETH` in the same multicall, excess ETH is not returned. [6](#0-5) [7](#0-6) 

However, `exactInputSingle` is also directly callable with `msg.value` (as shown in `test_mixedNativeAndWeth_exactInputSingle_wethForToken`), and there is no on-chain guard preventing a user from sending excess ETH in a direct call. [8](#0-7) 

## Impact Explanation

A user who calls `exactInputSingle{value: 200}(amountIn: 100, tokenIn: WETH)` directly (not via multicall) loses 100 ETH permanently. The stranded ETH sits in the router until any address calls `refundETH()`, at which point the caller receives the full balance. This is direct, irreversible loss of user principal with no privileged access required. Severity: **High** — direct loss of user funds, no access control required, no recovery path for the victim.

## Likelihood Explanation

The router exposes `exactInputSingle` as a standard `payable` external function. Users and integrators accustomed to Uniswap v3-style "send ETH, get tokens" UX will naturally call it directly with `msg.value > amountIn`. The multicall+refundETH requirement is only documented in a test-file comment, not enforced on-chain. The vulnerability is repeatable by any unprivileged caller and requires no special setup.

## Recommendation

Add an auto-refund at the end of every payable swap entry point:

```solidity
function exactInputSingle(ExactInputSingleParams calldata params)
    external payable returns (uint256 amountOut)
{
    // ... existing logic ...
    _clearExpectedCallbackPool();

    uint256 remaining = address(this).balance;
    if (remaining > 0) _transferETH(msg.sender, remaining);
}
```

Apply the same pattern to `exactInput`, `exactOutputSingle`, and `exactOutput`. Alternatively, restrict `refundETH` to only refund a tracked per-sender balance rather than the entire contract balance.

## Proof of Concept

```solidity
function test_excessEthStranded_claimedByAttacker() public {
    uint128 amountIn = 100;
    uint256 msgValue = 200; // 2x the needed amount
    address attacker = makeAddr("attacker");

    uint256 attackerEthBefore = attacker.balance;

    // User swaps WETH->token1 but sends 2x ETH, no refundETH call
    vm.prank(swapper);
    router.exactInputSingle{value: msgValue}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: amountIn,
            amountOutMinimum: 0,
            recipient: swapper,
            deadline: block.timestamp + 1000,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // 100 ETH is now stranded in the router
    assertEq(address(router).balance, 100, "excess ETH stranded");

    // Attacker claims it
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance - attackerEthBefore, 100, "attacker stole excess ETH");
    assertEq(address(router).balance, 0, "router drained");
}
```

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-71)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L83-86)
```text
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
  }
```

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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L8-10)
```text
/// @dev Native ETH flows follow Uniswap v3-periphery multicall patterns:
///      - ETH input: multicall{value}(exactInput*) with WETH as tokenIn
///      - ETH output: swap WETH to router, then unwrapWETH9 in the same multicall
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L51-64)
```text
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
