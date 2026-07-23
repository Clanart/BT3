### Title
Router `pay()` consumes stranded native ETH from prior transactions to settle subsequent WETH swaps, causing permanent fund loss for the original ETH depositor — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay()` helper in `PeripheryPayments.sol` settles WETH swap obligations by inspecting `address(this).balance` — the router's **total** native ETH balance — rather than only the ETH the current caller contributed via `msg.value`. When a user sends excess ETH in a WETH swap without appending a `refundETH()` call, the surplus is stranded on the router. Any subsequent WETH swap by a different user will silently consume that stranded ETH to satisfy its own payment obligation, permanently destroying the original depositor's funds while the second user pays nothing from their own balance.

---

### Finding Description

`PeripheryPayments.pay()` contains the following WETH branch:

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

When `nativeBalance >= value`, the function wraps and forwards exactly `value` ETH to the pool **without pulling any WETH from `payer`**. The `payer` field (the actual swap initiator stored in transient context) is completely bypassed. Because `nativeBalance` is the router's aggregate balance — including ETH left over from any prior transaction — a second caller whose `msg.value` is zero can have their entire WETH obligation satisfied by a previous user's stranded ETH.

The `refundETH()` helper that is supposed to recover excess ETH is:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
}
``` [2](#0-1) 

It is a **separate, optional call** — not automatically appended by any swap function. If the original depositor omits it (or if a second user's swap executes before the depositor can call it), the stranded ETH is irrecoverably consumed.

The swap functions `exactInputSingle`, `exactOutputSingle`, `exactInput`, and `exactOutput` are all `payable` and set `payer = msg.sender` for the first (or only) hop: [3](#0-2) [4](#0-3) 

The same `pay()` path is reused in `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback()` when `token0` or `token1` is WETH: [5](#0-4) 

---

### Impact Explanation

A user who sends excess ETH to a WETH swap (a standard pattern to avoid slippage) without bundling a `refundETH()` call loses the surplus permanently. The loss is triggered by any subsequent WETH swap from any address — an entirely unprivileged action. The original depositor's `refundETH()` call returns zero because the ETH has already been wrapped and forwarded to the pool on behalf of the second user. This is a direct, irreversible loss of user principal with no on-chain recovery path.

---

### Likelihood Explanation

Sending slightly more ETH than the exact swap amount is the standard defensive pattern for native-ETH swaps. The `multicall` + `refundETH()` idiom is documented in tests but is not enforced by the swap functions themselves. Any user who calls `exactInputSingle` (or the other swap entry points) directly with excess `msg.value` — without a trailing `refundETH()` — is immediately vulnerable. The trigger is any other user's WETH swap that follows in a subsequent block, which is a routine, zero-cost action.

---

### Recommendation

Track the ETH contributed by the current call separately from any pre-existing router balance. One approach: record `msg.value` at the top of each payable entry point and pass it explicitly into `pay()` as the maximum native ETH available for this call. Alternatively, enforce `msg.value == 0` unless the swap token is WETH and `msg.value == amountIn` exactly, and revert otherwise. At minimum, document that callers **must** append `refundETH()` in a multicall and add an internal assertion that the router's ETH balance after payment equals the pre-call balance minus the amount used.

---

### Proof of Concept

```
Block N:
  User A calls exactInputSingle{value: 2 ETH}(
      tokenIn=WETH, amountIn=1 ETH, recipient=A, ...
  )
  → _setNextCallbackContext(pool, JUST_PAY, payer=A, token=WETH)
  → pool.swap() triggers metricOmmSwapCallback
  → _justPayCallback → pay(WETH, A, pool, 1 ETH)
      nativeBalance = 2 ETH >= 1 ETH
      deposit(1 ETH) → transfer 1 WETH to pool   ✓
      1 ETH remains on router
  → A does NOT call refundETH()

Block N+1:
  User B calls exactInputSingle{value: 0}(
      tokenIn=WETH, amountIn=1 ETH, recipient=B, ...
  )
  → _setNextCallbackContext(pool, JUST_PAY, payer=B, token=WETH)
  → pool.swap() triggers metricOmmSwapCallback
  → _justPayCallback → pay(WETH, B, pool, 1 ETH)
      nativeBalance = 1 ETH (A's stranded ETH) >= 1 ETH
      deposit(1 ETH) → transfer 1 WETH to pool   ← A's ETH pays B's swap
      payer B is never charged
  → B receives swap output for free

User A calls refundETH():
  address(this).balance == 0  →  transfers 0 ETH to A
  A has permanently lost 1 ETH
```

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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L169-177)
```text
    PoolImmutables memory imm = IMetricOmmPool(msg.sender).getImmutables();
    address token0 = imm.token0;
    address token1 = imm.token1;
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```
