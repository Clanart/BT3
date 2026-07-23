### Title
Cross-User ETH Drain via Unattributed Native Balance in `pay()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments` uses the router's **entire** native ETH balance (`address(this).balance`) to cover any WETH-input payment, regardless of which user deposited that ETH. When a user calls a payable swap function (e.g., `exactOutputSingle`) with more ETH than the pool ultimately consumes, the surplus stays on the router. A subsequent user whose swap also uses WETH as `tokenIn` will have their payment silently subsidised by the first user's leftover ETH — effectively stealing it.

---

### Finding Description

`PeripheryPayments.pay()` handles WETH-input payments with this logic:

```solidity
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
}
``` [1](#0-0) 

The function reads `address(this).balance` — the router's **global** native balance — and uses it to satisfy the current caller's WETH obligation. There is no per-user attribution of that balance.

ETH accumulates on the router legitimately through any `payable` entry point. The most common path is `exactOutputSingle`: the user sends `msg.value = amountInMaximum` as a buffer because the exact input is unknown before the swap executes. The pool determines the real `amountIn`; the callback calls `pay()` which wraps only `amountIn` worth of ETH and forwards it to the pool. The remainder (`msg.value − amountIn`) stays on the router. [2](#0-1) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does **not** prevent ETH from accumulating via `msg.value` in any `payable` function. [3](#0-2) 

If the victim does not include a `refundETH()` call in the same `multicall`, or calls `exactOutputSingle` directly (not via `multicall`), the surplus ETH sits on the router until the next WETH-input swap drains it. [4](#0-3) 

The same `pay()` path is reachable from `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback` when `token0` or `token1` is WETH, so the drain also applies to liquidity-add flows. [5](#0-4) 

---

### Impact Explanation

**Direct loss of user ETH principal.** The victim's surplus ETH is consumed to pay a different user's swap obligation. The attacker pays zero WETH from their own wallet for the portion covered by the router's balance. The loss equals `min(victim_surplus, attacker_amountIn)` and can be up to the full `msg.value` the victim sent. No privileged role is required; any address can trigger the drain by submitting a WETH-input swap after the victim's transaction lands.

---

### Likelihood Explanation

`exactOutputSingle` is the canonical use-case for sending a native ETH buffer because the exact input is unknowable before execution. The Uniswap v3 multicall pattern (swap + `refundETH`) is well-documented, but:

- Users calling `exactOutputSingle` directly (not via `multicall`) have no in-transaction refund path.
- MEV searchers can observe the victim's transaction in the mempool and insert a WETH-input swap immediately after it, before the victim's `refundETH` call in a follow-up transaction.
- The `MetricOmmSimpleRouter` exposes `exactOutputSingle` as a standalone `payable` function, making the single-call (no-refund) pattern easy to reach. [6](#0-5) 

---

### Recommendation

Track the ETH that belongs to the **current caller** rather than reading the global balance. One approach: record `msg.value` in transient storage at the start of each entry point and consume only that amount in `pay()`, reverting if the caller's credited balance is insufficient. Alternatively, mirror Uniswap v3's documented guidance by making `exactOutputSingle` non-payable and requiring users to always go through `multicall` (which can include `refundETH`), so the surplus is always returned in the same atomic transaction.

---

### Proof of Concept

```solidity
// 1. Victim calls exactOutputSingle with 1 ETH buffer; pool only needs 0.5 ETH.
//    Surplus 0.5 ETH stays on the router.
router.exactOutputSingle{value: 1 ether}(ExactOutputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountOut: 1_000,
    amountInMaximum: 1 ether,
    recipient: victim,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// router.balance == 0.5 ether (victim's unrefunded surplus)

// 2. Attacker calls exactInputSingle with WETH as tokenIn, zero msg.value.
//    pay() sees nativeBalance = 0.5 ETH, wraps it, and forwards it to the pool.
//    Attacker's WETH allowance is NOT touched; victim's ETH pays the swap.
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 300,          // 300 wei < 0.5 ETH surplus
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Attacker receives token1 output; victim's ETH balance on router reduced by 300 wei.
// assertEq(address(router).balance, 0.5 ether - 300); // victim's ETH drained
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
