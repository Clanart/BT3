The code is clear. Let me trace the exact exploit path before writing the finding.

**`pay()` in `PeripheryPayments.sol` lines 73–84:**

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // <-- entire router ETH balance
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
```

`address(this).balance` is the **entire router's ETH balance**, not the current caller's `msg.value`. There is no per-user accounting. Any ETH left in the router from a prior unrefunded payable call is freely consumed by the next WETH-input swap.

---

### Title
Stranded ETH in Router Is Consumed by Subsequent WETH Swaps — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary

`PeripheryPayments.pay` uses `address(this).balance` as a funding source for any WETH-input swap, regardless of which user deposited that ETH. A user who sends excess ETH via a `payable` swap entry-point and does not immediately call `refundETH()` leaves ETH stranded in the router. A subsequent caller whose `tokenIn == WETH` will have their swap funded by that stranded ETH, consuming it without the original depositor's consent.

### Finding Description

`exactInputSingle` (and all other `payable` swap entry-points) accept `msg.value` but do not enforce that the full value is consumed or refunded atomically. The swap callback invokes `pay(tokenIn, payer, pool, amountOwed)`. Inside `pay`, when `token == WETH`: [1](#0-0) 

The branch reads `address(this).balance` — the **total** ETH held by the router — and wraps it as WETH to fund the swap. There is no check that this ETH was contributed by the current `payer` (`msg.sender` of the outer call). `refundETH()` is a separate, optional call: [2](#0-1) 

Because `refundETH` is not called atomically inside `exactInputSingle`, any ETH left over between two independent transactions is accessible to the next caller. [3](#0-2) 

### Impact Explanation

- **User A loses ETH**: their unrefunded ETH is wrapped and transferred to a pool to pay for User B's swap. User A receives nothing in return for this ETH.
- **User B gains a free swap**: their WETH-input swap is fully or partially funded by User A's ETH; User B's own WETH allowance is not drawn.
- Direct loss of user principal. Severity: **High**.

### Likelihood Explanation

- Any user who sends `msg.value > amountIn` (e.g., to avoid reverting on partial fills) and does not bundle `refundETH()` in the same `multicall` creates the vulnerable state.
- The attacker only needs to observe the router's ETH balance (trivially on-chain) and call `exactInputSingle` with `tokenIn=WETH` and `amountIn <= router.balance`.
- No special permissions, no malicious pool, no non-standard tokens required.

### Recommendation

Track the ETH contributed by the current top-level call in transient storage (e.g., store `msg.value` at entry and deduct from it inside `pay`). Only use the tracked per-call ETH balance, not `address(this).balance`, when wrapping ETH for WETH payments. Alternatively, enforce that `refundETH()` is called atomically at the end of every payable entry-point (e.g., inside `exactInputSingle` itself before returning).

### Proof of Concept

```
1. User A: exactInputSingle{value: 1 ether}(tokenIn=WETH, amountIn=0.5 ether, ...)
   - Callback: pay(WETH, userA, pool, 0.5 ether)
     → nativeBalance = 1 ether ≥ 0.5 ether
     → wraps 0.5 ETH, sends WETH to pool ✓
   - 0.5 ETH remains in router. User A does NOT call refundETH().

2. User B: exactInputSingle{value: 0}(tokenIn=WETH, amountIn=0.5 ether, ...)
   - Callback: pay(WETH, userB, pool, 0.5 ether)
     → nativeBalance = 0.5 ether (User A's leftover) ≥ 0.5 ether
     → wraps 0.5 ETH (User A's), sends WETH to pool ✓
     → User B's WETH allowance: NOT touched (safeTransferFrom never called)

Result: User A loses 0.5 ETH. User B's swap is fully subsidized.
``` [4](#0-3)

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
