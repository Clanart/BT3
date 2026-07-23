### Title
`exactOutput` Multi-Hop Intermediate Hops Use Exact-Input Semantics Instead of Exact-Output, Making the Function Permanently Broken for Non-1:1 Price Pairs - (File: metric-periphery/contracts/MetricOmmSimpleRouter.sol)

---

### Summary

`MetricOmmSimpleRouter.exactOutput` is the multi-hop exact-output swap entry point. Its single-hop counterpart `exactOutputSingle` correctly passes a **negative** `amountSpecified` to the pool (exact-output semantics). However, the recursive callback `_exactOutputIterateCallback` passes a **positive** `amountSpecified` to every intermediate pool via `MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay)` (exact-input semantics). Because the intermediate pool's output amount is price-dependent and will not equal its input amount for any non-1:1 price pair, the strict equality check `if (amountOut != amountToPay) revert InvalidOutputAmountAtHop(...)` causes every multi-hop `exactOutput` call to revert unconditionally for real-world price pairs.

---

### Finding Description

**Asymmetry between `exactOutputSingle` and `exactOutput` intermediate hops:**

`exactOutputSingle` correctly negates the desired output amount before calling the pool:

```solidity
// MetricOmmSimpleRouter.sol line 134-137
int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
...
.swap(params.recipient, params.zeroForOne, -expectedAmountOut, ...)
```

A negative `amountSpecified` tells the pool "deliver exactly this many output tokens; charge me whatever input is required."

In `_exactOutputIterateCallback`, the intermediate pool is called with a **positive** amount:

```solidity
// MetricOmmSimpleRouter.sol line 220-228
(int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
    .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),  // POSITIVE = exact-input
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
    );
```

`amountToPay` is the positive delta the current pool needs as its input token. The intermediate pool is therefore asked to **consume** `amountToPay` of its own input token and produce some price-dependent quantity of its output token. That output quantity is then compared against `amountToPay`:

```solidity
// MetricOmmSimpleRouter.sol line 230-232
int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0DeltaReturned, amount1DeltaReturned);
if (amountOut != amountToPay) revert InvalidOutputAmountAtHop(tradesLeft, amountOut, amountToPay);
```

For any price ratio other than exactly 1:1, `amountOut ≠ amountToPay`, and the entire transaction reverts.

The correct fix is to pass `-amountToPay` (exact-output) so the intermediate pool delivers exactly the required amount regardless of price.

The code comment at line 150-153 even documents the wrong design:

> "each callback pays the current hop's input, then (unless on the last pool) swaps the next pool **for exactly that input amount**"

"for exactly that input amount" describes exact-input semantics, which is the wrong direction for an exact-output multi-hop.

---

### Impact Explanation

`MetricOmmSimpleRouter.exactOutput` is completely unusable for any token pair whose exchange rate is not exactly 1:1 (i.e., virtually every real-world pool). Every call reverts at `InvalidOutputAmountAtHop`. This is a broken core swap flow explicitly listed in the allowed impact gate: *"Broken core pool functionality causing loss of funds or unusable withdraw/swap/liquidity flows."*

`exactOutputSingle` (single-hop) is unaffected and works correctly.

---

### Likelihood Explanation

The breakage is deterministic and unconditional: any caller invoking `exactOutput` with two or more hops on pools whose price is not exactly 1:1 will always revert. No special attacker setup is required; ordinary users attempting multi-hop exact-output swaps trigger the revert on every attempt.

---

### Recommendation

In `_exactOutputIterateCallback`, negate `amountToPay` when calling the intermediate pool so it uses exact-output semantics:

```solidity
// Before (broken):
MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay)

// After (correct):
-MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay)
// or equivalently:
MetricOmmSwapInputs.asAmountSpecifiedOut(amountToPay)  // if such a helper exists
```

This mirrors how `exactOutputSingle` and the outermost `exactOutput` call negate the desired output amount before passing it to the pool.

---

### Proof of Concept

Consider a 2-hop exact-output swap: USDC → WETH → DAI, wanting exactly 1000 DAI out.

1. `exactOutput` calls `pool_WETH_DAI` with `amountSpecified = -1000e18` (exact output of 1000 DAI). Pool sends 1000 DAI to recipient and calls callback needing, say, `0.5e18` WETH (`amountToPay = 0.5e18`).

2. `_exactOutputIterateCallback` calls `pool_USDC_WETH` with `amountSpecified = +0.5e18` (exact **input** of 0.5e18 USDC). Pool consumes 0.5e18 USDC and produces, say, `0.000278 WETH` (at ~1800 USDC/WETH).

3. Check: `amountOut (0.000278e18) != amountToPay (0.5e18)` → `revert InvalidOutputAmountAtHop(0, ...)`.

The transaction reverts despite the user having sufficient funds and the pools having sufficient liquidity. The function is permanently broken for this (and every other non-1:1) price pair. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L149-153)
```text
  /// @inheritdoc IMetricOmmSimpleRouter
  /// @dev Starts at `pools[last]` with a negative `amountSpecified` for the final output token. Remaining hops run
  ///      recursively inside `metricOmmSwapCallback`: each callback pays the current hop's input, then (unless on
  ///      the last pool) swaps the next pool for exactly that input amount. The first swap's input delta is total
  ///      `amountIn`.
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L201-233)
```text
  function _exactOutputIterateCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) private {
    ExactOutputIterateCallbackData memory cb = abi.decode(data, (ExactOutputIterateCallbackData));

    int256 amountToPay = MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta);
    uint8 tradesLeft = _getTradesLeft();

    if (tradesLeft == 0) {
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 amountIn = uint256(amountToPay);
      if (amountIn > cb.amountInMax) revert InputTooHigh(amountIn, cb.amountInMax);
      _setExactOutputAmountIn(amountIn);
      pay(_getTokenToPay(), _getPayer(), msg.sender, amountIn);
      return;
    }
    tradesLeft--;
    address pool = cb.pools[tradesLeft];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(cb.zeroForOneBitMap, tradesLeft);
    _updateCallbackContextforRecursiveOutput(pool, tradesLeft);

    (int128 amount0DeltaReturned, int128 amount1DeltaReturned) = IMetricOmmPoolActions(pool)
      .swap(
        msg.sender,
        zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedFromPositive(amountToPay),
        MetricOmmSwapPath.openLimit(zeroForOne),
        data,
        cb.extensionDatas[tradesLeft]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0DeltaReturned, amount1DeltaReturned);

    if (amountOut != amountToPay) revert InvalidOutputAmountAtHop(tradesLeft, amountOut, amountToPay);
  }
```
