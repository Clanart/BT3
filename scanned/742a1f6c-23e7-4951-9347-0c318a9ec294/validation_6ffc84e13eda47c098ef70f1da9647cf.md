### Title
Any caller can steal ETH residue stranded on the router via `refundETH()` / `sweepToken()` / `unwrapWETH9()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments` exposes three public helpers — `refundETH()`, `sweepToken()`, and `unwrapWETH9()` — with **no access control and no attribution to the original depositor**. The `pay()` function's WETH branch consumes only the exact swap amount from `address(this).balance`, leaving any excess native ETH on the router. An attacker watching the mempool can immediately call `refundETH()` in a follow-up transaction to steal that residue.

---

### Finding Description

**Root cause — `pay()` leaves ETH residue:** [1](#0-0) 

When `token == WETH` and `nativeBalance >= value`, the function deposits exactly `value` as WETH and transfers it to the pool. The remaining `nativeBalance - value` native ETH is **silently left on the router** with no accounting of who deposited it.

**Root cause — unrestricted public helpers:** [2](#0-1) 

`refundETH()` is `external payable` with no access control. It sends the **entire** ETH balance of the router to `msg.sender` — any caller, not the original depositor. [3](#0-2) 

`sweepToken()` is `public payable` with no access control. It sends the **entire** ERC-20 balance to a **caller-chosen** `recipient`. [4](#0-3) 

`unwrapWETH9()` is `public payable` with no access control. It unwraps all WETH and sends ETH to a **caller-chosen** `recipient`.

**Trigger path — `exactOutputSingle` with excess `msg.value`:** [5](#0-4) 

`exactOutputSingle` is `external payable`. A user who does not know the exact `amountIn` in advance sends `msg.value = amountInMaximum`. The pool determines the actual `amountIn < amountInMaximum`. The callback calls `pay(WETH, user, pool, amountIn)`, which deposits only `amountIn` as WETH. The surplus `msg.value - amountIn` ETH remains on the router with no record of ownership.

The same residue arises from `exactInputSingle{value: X}` when `X > amountIn`: [6](#0-5) 

---

### Impact Explanation

An attacker monitoring the mempool calls `refundETH()` immediately after the victim's swap transaction is confirmed. The attacker receives `msg.value - amountIn` ETH that belongs to the victim. For `exactOutputSingle` with a generous `amountInMaximum` (e.g., 2× the expected cost), the loss can exceed 50 % of the ETH the user committed, trivially clearing the Critical threshold (>20 %, >$100).

`sweepToken(token, 0, attacker)` and `unwrapWETH9(0, attacker)` extend the same theft to any ERC-20 or WETH residue that reaches the router between transactions.

---

### Likelihood Explanation

- `exactOutputSingle` is the canonical "I want exactly X output tokens" flow; users routinely set `amountInMaximum` well above the expected cost to avoid slippage reverts.
- Any user who calls `exactOutputSingle` or `exactInputSingle` directly (not wrapped in a `multicall` that also calls `refundETH`) leaves residue.
- No privileged access is required; a single `refundETH()` call from any EOA suffices.
- The attack is atomic and MEV-bot-friendly: the attacker can sandwich the victim's swap with a `refundETH()` call in the very next block.

---

### Recommendation

1. **Track per-depositor balances**: record `msg.value` credited to `msg.sender` at each payable entry point and allow only that address to reclaim its share via `refundETH`.
2. **Restrict helpers to `msg.sender` only**: remove the caller-chosen `recipient` parameter from `sweepToken` and `unwrapWETH9`, or gate them so only the address that deposited in the same multicall context can invoke them.
3. **Auto-refund in swap functions**: after `pay()` consumes the required WETH amount, immediately refund `address(this).balance` to `msg.sender` inside `exactInputSingle` / `exactOutputSingle` rather than relying on a separate `refundETH` call.

---

### Proof of Concept

```
// Victim transaction
router.exactOutputSingle{value: 2 ether}(ExactOutputSingleParams({
    pool:            address(wethPool),
    tokenIn:         WETH,
    tokenOut:        token1,
    zeroForOne:      true,
    amountOut:       1_000,
    amountInMaximum: 2 ether,   // generous cap
    recipient:       victim,
    deadline:        block.timestamp + 60,
    priceLimitX64:   0,
    extensionData:   ""
}));
// Pool charges amountIn = 1 ether.
// pay() deposits 1 ether as WETH → pool.
// 1 ether remains on router, unattributed.

// Attacker transaction (next block or same block via MEV)
// address(router).balance == 1 ether
router.refundETH();   // attacker receives 1 ether
// Victim lost 1 ether (~$3 000 at current prices).
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L37-45)
```text
  function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);

    if (balanceWETH > 0) {
      IWETH9(WETH).withdraw(balanceWETH);
      _transferETH(recipient, balanceWETH);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L48-55)
```text
  function sweepToken(address token, uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceToken = IERC20(token).balanceOf(address(this));
    if (balanceToken < amountMinimum) revert InsufficientToken(token, amountMinimum, balanceToken);

    if (balanceToken > 0) {
      IERC20(token).safeTransfer(recipient, balanceToken);
    }
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
