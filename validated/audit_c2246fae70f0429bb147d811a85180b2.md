Audit Report

## Title
`exactInput` multi-hop router reverts on valid partial-fill swaps due to strict `amountInActual < amount` guard — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

## Summary

`MetricOmmSimpleRouter.exactInput()` enforces a strict per-hop check that the pool consumed the full requested input amount. The MetricOmm pool is documented and test-proven to return partial fills (consuming less than `amountSpecified`) whenever the drift limit or liquidity boundary is reached. When this occurs on any hop of a multi-hop path, the router reverts unconditionally, making the entire multi-hop exact-input path unusable under normal market conditions. The same structural flaw exists in `_exactOutputIterateCallback` with a strict `!=` equality check.

## Finding Description

In `exactInput`, after each pool call the router runs:

```solidity
// MetricOmmSimpleRouter.sol L114-115
int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);
``` [1](#0-0) 

`amount` is the full input requested for that hop (positive `int128`). `amountInActual` is the amount the pool actually consumed. The pool's swap engine is explicitly designed to stop early and return a partial fill when the drift cap (MAX_DRIFT = 5%) is reached or all liquidity bins are exhausted — the pool does **not** revert in those cases; it simply returns `amount1Delta < amountSpecified`.

The pool's own test suite confirms this behaviour explicitly:

```solidity
// metric-core/test/MetricOmmPool.swap.t.sol L643-657
function test_exactInput_consumesLess_whenDriftLimitHit_token1ForToken0() public {
    uint128 hugeAmountIn = 10000000;
    ...
    assertLt(_u128FromNonNegDelta(amount1Delta), hugeAmountIn,
        "Should consume less than specified when drift limit hit");
}
``` [2](#0-1) [3](#0-2) 

The same strict-equality pattern appears in `_exactOutputIterateCallback`:

```solidity
// MetricOmmSimpleRouter.sol L230-232
int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0DeltaReturned, amount1DeltaReturned);
if (amountOut != amountToPay) revert InvalidOutputAmountAtHop(tradesLeft, amountOut, amountToPay);
``` [4](#0-3) 

Here the intermediate pool is called with exact-output semantics (`-amountToPay`). If that pool cannot fill the full amount (liquidity exhausted or drift limit hit), it returns a partial fill, `amountOut < amountToPay`, and the strict `!=` check reverts the entire transaction.

Existing guards are insufficient: the final `amountOutMinimum` check at L122 is never reached because the revert occurs inside the loop at L115. [5](#0-4) 

## Impact Explanation

Any multi-hop `exactInput` or `exactOutput` call that routes through a pool operating near its drift cap or near liquidity exhaustion will revert unconditionally, even when the user's `amountOutMinimum` / `amountInMaximum` slippage guard would have been satisfied. The multi-hop swap path — the primary UX surface of the periphery — becomes unreliable or completely unusable under normal high-volume or low-liquidity market conditions. This constitutes broken core swap functionality causing unusable swap flows, matching the allowed impact gate.

## Likelihood Explanation

The drift limit (MAX_DRIFT = 5%) is a normal, expected pool behaviour triggered by any sufficiently large swap relative to the pool's liquidity. It is not an edge case: the pool's own test suite has dedicated tests for it (`test_exactInput_consumesLess_whenDriftLimitHit_token1ForToken0`, `test_swap_insufficientLiquidity`). Any multi-hop route that passes through a pool where any hop is large relative to available liquidity will trigger the revert. No privileged access or malicious setup is required; an ordinary user submitting a valid multi-hop swap is sufficient.

## Recommendation

**For `exactInput`:** Remove the strict `amountInActual < amount` revert. Propagate the actual output (`extractAmountOut`) as the input to the next hop, and let the final `amountOutMinimum` guard protect the user:

```solidity
// Remove:
if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

// Keep only:
amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
```

**For `_exactOutputIterateCallback`:** Replace the strict `!=` check with a `<` check, or propagate the actual amount received and let the outer `InvalidOutputAmount` guard on the final hop enforce the invariant.

## Proof of Concept

1. Deploy two pools, each with modest liquidity (sufficient for a 1,000-unit swap but not a 10,000-unit swap).
2. Call `exactInput` with `amountIn = 10_000`, `amountOutMinimum = 0`, routing through both pools.
3. The first pool hits its drift limit and returns `amount1Delta = 8_000 < 10_000`.
4. The router evaluates `amountInActual (8_000) < amount (10_000)` → `true` → reverts with `InvalidInputAmountAtHop(0, 8000, 10000)`.
5. The swap fails even though the user set `amountOutMinimum = 0` and would have accepted any output.

This is directly reproducible using the existing pool test infrastructure in `metric-core/test/MetricOmmPool.swap.t.sol` by composing two pools and calling the router's `exactInput` with an amount large enough to trigger the drift limit on the first hop. [6](#0-5)

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L120-123)
```text
    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L230-232)
```text
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0DeltaReturned, amount1DeltaReturned);

    if (amountOut != amountToPay) revert InvalidOutputAmountAtHop(tradesLeft, amountOut, amountToPay);
```

**File:** metric-core/test/MetricOmmPool.swap.t.sol (L302-323)
```text
  /// @notice Test swap when there's insufficient liquidity
  function test_swap_insufficientLiquidity() public {
    // Try to swap more than available using exact input
    // This should cap the output at what's available
    uint128 hugeAmountIn = 10000000; // Much more than needed for all liquidity

    uint256 token0PoolBalance = token0.balanceOf(address(pool));

    (int256 amount0, int256 amount1) =
      _swap(
        SWAPPER_INDEX,
        swapper,
        false,
        _i128ExactIn(hugeAmountIn), // exact input - positive
        type(uint128).max
      );

    // Should receive at most what's available (will hit drift limit before exhausting liquidity)
    assertLe(_u128FromNegDelta(amount0), token0PoolBalance, "Cannot receive more than pool balance");
    // Should use less input than specified since we hit limits
    assertLt(_u128FromNonNegDelta(amount1), hugeAmountIn, "Should not use all input due to drift limit");
  }
```

**File:** metric-core/test/MetricOmmPool.swap.t.sol (L643-657)
```text
  /// @notice When exact input hits drift limit, consumed amount should be less than specified
  function test_exactInput_consumesLess_whenDriftLimitHit_token1ForToken0() public {
    // Large swap that will hit drift limit (MAX_DRIFT is 5%)
    // Need a VERY large amount to hit drift on token1->token0 direction
    uint128 hugeAmountIn = 10000000;

    (int256 amount0Delta, int256 amount1Delta) =
      _swap(SWAPPER_INDEX, swapper, false, _i128ExactIn(hugeAmountIn), type(uint128).max);

    assertLt(
      _u128FromNonNegDelta(amount1Delta), hugeAmountIn, "Should consume less than specified when drift limit hit"
    );
    assertGt(amount1Delta, 0, "Should have consumed some input");
    assertLt(amount0Delta, 0, "Should have received some output");
  }
```
