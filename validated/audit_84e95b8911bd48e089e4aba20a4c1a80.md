The `pay` function in `PeripheryPayments.sol` is the crux. Let me trace the exact path:

**Step 1 — User A sends excess ETH:**
`exactInputSingle` is `payable`. User A calls it with `{value: 1 ether}`, `tokenIn=WETH`, `amountIn=0.5 ether`. Inside `_justPayCallback`, `pay(WETH, userA, pool, 0.5e18)` is called. [1](#0-0) 

`address(this).balance == 1 ether >= 0.5 ether`, so the branch wraps 0.5 ETH from the contract's native balance and transfers WETH to the pool. The remaining **0.5 ETH stays on the router**. User A does not call `refundETH()`. [2](#0-1) 

**Step 2 — User B exploits the stranded ETH:**
User B calls `exactInputSingle{value: 0}` with `tokenIn=WETH`, `amountIn=0.5 ether`. `pay(WETH, userB, pool, 0.5e18)` is called. Now `address(this).balance == 0.5 ether >= 0.5 ether`, so the same branch fires: it wraps **User A's 0.5 ETH** and sends WETH to the pool. User B's WETH allowance is never touched. [3](#0-2) 

The `receive()` guard only blocks non-WETH ETH arriving via `receive()`, but `msg.value` in payable calls bypasses it entirely — the ETH accumulates in `address(this).balance` regardless. [4](#0-3) 

`refundETH()` is a separate, optional call — it is never invoked automatically at the end of `exactInputSingle`. [5](#0-4) 

The proof-of-concept scenario in the question is mechanically sound. The vulnerability is real.

---

### Title
Stranded ETH on Router Consumed for Subsequent Caller's WETH Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.pay` uses `address(this).balance` — the router's total native ETH balance — when deciding how to fund a WETH payment. Any ETH left on the router from a prior user's unrefunded `msg.value` is silently consumed to fund a later user's WETH swap, violating the invariant that each user's payment is funded exclusively from their own assets.

### Finding Description
When `pay(WETH, payer, recipient, value)` is called and `payer != address(this)`, the function checks `address(this).balance` against `value`:

```solidity
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

`address(this).balance` is a global contract state value, not scoped to the current caller. Any ETH deposited by a prior `msg.value` call that was not refunded is indistinguishable from ETH the current caller sent. The function will wrap and spend it for the current caller's benefit without pulling from the current caller's WETH allowance.

### Impact Explanation
- **User A loses funds**: their unrefunded ETH is consumed without consent by a third party.
- **User B gains a free subsidy**: their WETH allowance is not spent; they receive the swap output funded by User A's ETH.
- Direct loss of user principal (User A's ETH). High severity under Sherlock thresholds.

### Likelihood Explanation
Any two sequential WETH-input swaps where the first caller sends excess ETH and omits `refundETH()` trigger this. This is a realistic user error (forgetting to bundle `refundETH` in a `multicall`), and an attacker can deliberately front-run or sequence calls to exploit it. No privileged access is required.

### Recommendation
Track per-call ETH contribution in transient storage (set to `msg.value` at entry, cleared at exit) and limit `pay`'s native-balance draw to that amount. Alternatively, automatically refund excess ETH at the end of each swap entry point, or require that the native balance used in `pay` equals exactly `msg.value` of the current call.

### Proof of Concept
1. User A: `exactInputSingle{value: 1 ether}(tokenIn=WETH, amountIn=0.5 ether, ...)` — 0.5 ETH consumed for swap, 0.5 ETH stranded on router.
2. User A does not call `refundETH()`.
3. User B: `exactInputSingle{value: 0}(tokenIn=WETH, amountIn=0.5 ether, ...)` — `pay` sees `address(this).balance == 0.5 ether >= 0.5 ether`, wraps User A's ETH, sends WETH to pool. User B's WETH allowance untouched.
4. Assert: User A's 0.5 ETH is gone; User B received a fully-funded swap at zero cost.

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
