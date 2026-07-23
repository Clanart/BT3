Audit Report

## Title
`exactInputSingle` Missing Zero-Output Guard Allows Silent Input Token Loss When `amountOutMinimum = 0` — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

## Summary
`exactInputSingle` omits the explicit `out <= 0` guard that `exactInput` enforces after each hop. When the pool's `deltasScaledToExternal` conversion rounds a small scaled output to zero external units (possible for tokens with fewer than 18 decimals where `TOKEN_X_SCALE_MULTIPLIER > 1`), the router accepts the result silently if the caller supplied `amountOutMinimum = 0`, consuming the user's input tokens while delivering nothing.

## Finding Description
`exactInput` (multi-hop) enforces a post-loop guard at line 120:
```solidity
if (amount <= 0) revert InvalidSwapDeltas();
```
`exactInputSingle` (single-hop) has no equivalent. Its only post-swap guard is the caller-supplied minimum at lines 81–83:
```solidity
int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
amountOut = MetricOmmSwapInputs.int128ToUint128(out);
if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
```
When `amountOutMinimum = 0` and `amountOut = 0`, the check `0 < 0` is `false` — no revert.

The pool's `deltasScaledToExternal` uses `SignedMath.ceilDiv` for both deltas:
```solidity
deltaAmount0 = SignedMath.ceilDiv(scaledDeltaAmount0, TOKEN_0_SCALE_MULTIPLIER);
deltaAmount1 = SignedMath.ceilDiv(scaledDeltaAmount1, TOKEN_1_SCALE_MULTIPLIER);
```
For a token with fewer than 18 decimals (e.g., 6-decimal token where `TOKEN_1_SCALE_MULTIPLIER = 10^12`), if the scaled output `amount1DeltaScaled = -1`, then `ceilDiv(-1, 10^12) = ceil(-10^-12) = 0`. Meanwhile, the positive input delta `amount0DeltaScaled >= 1` gives `deltaAmount0 >= 1` via ceiling division. The result is `amount0Delta > 0` and `amount1Delta == 0`.

The callback guard at line 47 does not save the user:
```solidity
if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas();
```
With `amount0Delta > 0` and `amount1Delta == 0`, the condition `amount0Delta <= 0 && amount1Delta == 0` evaluates to `false && true = false` — no revert. The callback proceeds to pay the pool via `_justPayCallback`, and the transaction completes successfully with the user holding zero output tokens.

## Impact Explanation
A user calling `exactInputSingle` with a dust `amountIn` and `amountOutMinimum = 0` loses their entire input amount with no output received. The pool's `binTotals.scaledToken0` increases (it received the input via the callback), the recipient receives nothing, and the transaction emits no revert. This is a direct, silent loss of user principal with no recovery path. The loss per transaction is bounded by the dust threshold (input amount that produces < 1 external unit of output), but it is unrecoverable and repeatable.

## Likelihood Explanation
`amountOutMinimum = 0` is a valid, documented parameter value per `IMetricOmmSimpleRouter.ExactInputSingleParams`. Integrators, aggregators, and users testing swaps routinely omit slippage protection. The condition is reachable by any unprivileged caller with no special setup — only a pool with a token having fewer than 18 decimals (common: USDC, USDT, WBTC) and a dust-sized input are required. The asymmetry between `exactInput` (guarded at line 120) and `exactInputSingle` (unguarded) confirms the developers recognized the zero-output scenario as real.

## Recommendation
Add an explicit zero-output guard in `exactInputSingle`, mirroring the guard already present in `exactInput`:
```solidity
int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
if (out <= 0) revert InvalidSwapDeltas();   // ← add this
amountOut = MetricOmmSwapInputs.int128ToUint128(out);
if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
```

## Proof of Concept
1. Deploy a pool with a 6-decimal output token (`TOKEN_1_SCALE_MULTIPLIER = 10^12`) and a price such that 1 wei of `tokenIn` maps to 1 scaled unit of `tokenOut` (< `10^12` external units → rounds to 0).
2. Approve the router for `tokenIn`.
3. Call:
   ```solidity
   router.exactInputSingle(ExactInputSingleParams({
       pool: pool, tokenIn: tokenIn, tokenOut: tokenOut,
       zeroForOne: true, amountIn: 1, amountOutMinimum: 0,
       recipient: user, deadline: block.timestamp + 1,
       priceLimitX64: 0, extensionData: ""
   }));
   ```
4. Observe: `tokenIn` balance of caller decreases by 1 wei; `tokenOut` balance of recipient unchanged; transaction succeeds without revert.
5. Pool's `binTotals.scaledToken0` increased; user's funds are permanently lost.

**Supporting code references:**

- Missing guard in `exactInputSingle`: [1](#0-0) 
- Guard present in `exactInput`: [2](#0-1) 
- Callback guard insufficient for `amount0Delta > 0, amount1Delta == 0`: [3](#0-2) 
- `deltasScaledToExternal` rounding that produces zero external output from non-zero scaled output: [4](#0-3) 
- Output rounds down by design in `calculateOutputToken1FromBinPosition`: [5](#0-4)

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L47-47)
```text
    if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas();
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L81-83)
```text
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L120-120)
```text
    if (amount <= 0) revert InvalidSwapDeltas();
```

**File:** metric-core/contracts/MetricOmmPool.sol (L612-613)
```text
    deltaAmount0 = SignedMath.ceilDiv(scaledDeltaAmount0, TOKEN_0_SCALE_MULTIPLIER);
    deltaAmount1 = SignedMath.ceilDiv(scaledDeltaAmount1, TOKEN_1_SCALE_MULTIPLIER);
```

**File:** metric-core/contracts/libraries/SwapMath.sol (L188-191)
```text
    unchecked {
      // Product ≤ 2^104 × 2^104 = 2^208. Quotient ≤ availableToken1 ≤ MAX_POS_BIN.
      outToken1 = (availableToken1 * uint256(currBinPos - finalBinPos)) / currBinPos;
    }
```
