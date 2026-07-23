### Title
Multi-hop `exactInput` Swap Permanently Broken When USDT (Non-Zero Fee) Is an Intermediate Token - (File: `metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

In `MetricOmmSimpleRouter.exactInput`, the router uses the pool's reported output delta as the exact amount to forward to the next pool. When USDT (with a non-zero transfer fee) is an intermediate token, the router receives `amountOut - fee` from Pool[i] but then attempts to `safeTransfer` the full `amountOut` to Pool[i+1], causing an irreversible revert due to insufficient balance. All multi-hop routes through USDT as an intermediate token become permanently unusable.

---

### Finding Description

The `exactInput` loop in `MetricOmmSimpleRouter` walks pools forward, using each hop's output as the next hop's input:

```solidity
// MetricOmmSimpleRouter.sol lines 99–118
for (uint256 i = 0; i <= last; i++) {
    _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY,
        i == 0 ? msg.sender : address(this),   // ← payer is router for i > 0
        params.tokens[i]);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(i == last ? params.recipient : address(this), ...);

    amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    // ← amount = pool's *reported* delta, not actual tokens received
}
```

For hop `i = 0`, Pool[0] sends USDT to `address(this)` (the router). Because USDT applies a transfer fee, the router actually receives `amountOut - fee`. The variable `amount` is set to the pool's reported `amountOut` (the accounting delta), not the actual balance received.

For hop `i = 1`, the callback context sets `payer = address(this)`. When Pool[1] calls back `metricOmmSwapCallback`, `_justPayCallback` executes:

```solidity
// MetricOmmSimpleRouter.sol lines 192–199
function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
        _getTokenToPay(),
        _getPayer(),          // address(this)
        msg.sender,           // Pool[1]
        uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
}
```

Inside `pay()`, since `payer == address(this)`:

```solidity
// PeripheryPayments.sol lines 71–72
if (payer == address(this)) {
    IERC20(token).safeTransfer(recipient, value);  // value = amountOut, but balance = amountOut - fee
}
```

The router holds only `amountOut - fee` USDT but attempts to transfer `amountOut`. `safeTransfer` reverts with insufficient balance. The entire transaction is rolled back.

There is no guard anywhere in the loop that verifies the router's actual token balance after receiving from Pool[i] before forwarding to Pool[i+1].

---

### Impact Explanation

Any multi-hop swap route where USDT is an intermediate token (not the first input or final output) becomes completely unusable when USDT's transfer fee is non-zero. The transaction always reverts at the second hop's callback payment. This is a broken core swap flow — a direct analog to the compound-protocol flashloan repayment revert caused by USDT fee.

---

### Likelihood Explanation

USDT's fee is currently set to zero but the fee mechanism exists in the contract and can be enabled by the USDT owner at any time. The protocol explicitly supports USDT (it is carved out from the "non-standard ERC20 out-of-scope" rule). Any integrator routing through USDT as an intermediate hop would be permanently broken the moment USDT enables its fee.

---

### Recommendation

After each intermediate hop, measure the router's actual received balance rather than trusting the pool's reported delta. Replace:

```solidity
amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
```

with a balance-snapshot approach:

```solidity
uint256 balanceBefore = IERC20(params.tokens[i + 1]).balanceOf(address(this));
// ... swap call ...
uint256 balanceAfter = IERC20(params.tokens[i + 1]).balanceOf(address(this));
amount = MetricOmmSwapInputs.toInt128(uint128(balanceAfter - balanceBefore));
```

This ensures the next hop is funded with the actual received amount, not the accounting delta.

---

### Proof of Concept

**Setup:** Two pools, A→USDT (Pool[0]) and USDT→B (Pool[1]). USDT fee = 1% (100 bps).

1. User calls `exactInput` with `amountIn = 1000 A`, path `[A, USDT, B]`.
2. Hop 0: Pool[0] swaps 1000 A → 500 USDT. Pool[0] calls `metricOmmSwapCallback`; router pays 1000 A from `msg.sender`. Pool[0] sends 500 USDT to `address(router)`. Due to USDT fee, router receives **495 USDT**.
3. `amount` is set to `500` (pool's reported delta).
4. Hop 1: Router calls `Pool[1].swap(recipient, ..., amountSpecified=500, ...)`.
5. Pool[1] calls `metricOmmSwapCallback`; `_justPayCallback` calls `pay(USDT, address(this), Pool[1], 500)`.
6. `safeTransfer(Pool[1], 500)` reverts — router only holds **495 USDT**.
7. Entire transaction reverts. Multi-hop USDT route is permanently broken. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-118)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
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
