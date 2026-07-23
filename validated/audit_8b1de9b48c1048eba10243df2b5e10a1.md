### Title
Swap input balance check uses `>` instead of `!=`, silently absorbing excess tokens sent by callback without returning corresponding output — (`metric-core/contracts/MetricOmmPool.sol`)

---

### Summary

`MetricOmmPool.swap` checks that the callback paid *at least* the required input amount, but does not reject *overpayment*. Any `IMetricOmmSwapCallback` implementation that transfers more than the computed `amount0Delta` / `amount1Delta` causes the pool to permanently absorb the surplus tokens while the user receives only the pre-computed output. The user loses the excess input with zero compensation.

---

### Finding Description

After computing the swap deltas and transferring the output tokens to `recipient`, `MetricOmmPool.swap` records the pre-callback balance and then invokes the caller's `metricOmmSwapCallback`. The post-callback invariant check is:

```solidity
// zeroForOne branch (MetricOmmPool.sol line 261)
if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
    revert IncorrectDelta();
}

// !zeroForOne branch (MetricOmmPool.sol line 275)
if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
    revert IncorrectDelta();
}
```

The condition reverts only when `balance() < balance_before + required`. It does **not** revert when `balance() > balance_before + required` — i.e., when the callback overpays. The surplus tokens are silently retained by the pool. Because `amount0Delta` / `amount1Delta` are fixed before the callback is called, no additional output is ever sent to the recipient for the extra input.

The `IMetricOmmSwapCallback` interface is public and intended to be implemented by third parties. Any integrator whose `metricOmmSwapCallback` transfers `required + N` tokens (due to a bug, rounding, or deliberate design) will lose `N` tokens to the pool with no recourse. [1](#0-0) [2](#0-1) 

---

### Impact Explanation

**Direct loss of user principal.** Tokens transferred in excess of `amount0Delta` / `amount1Delta` are permanently locked in the pool. The pool's internal bin accounting does not record the surplus (it was not part of the swap computation), so the tokens are unclaimable by any LP or the user. The loss is proportional to the overpayment and occurs silently — no event or revert signals the discrepancy. [3](#0-2) 

---

### Likelihood Explanation

The `IMetricOmmSwapCallback` interface is explicitly designed for third-party implementation. [4](#0-3) 

The built-in `MetricOmmSimpleRouter` pays exactly the right amount via `_justPayCallback`, so the router itself is not affected. However, any external integrator building a custom router, aggregator, or wrapper that accidentally sends a rounded-up or over-approved amount will silently lose funds. The scenario is realistic for integrators who use `transferFrom` with a pre-approved allowance and compute the amount independently, or who add a small buffer to guarantee the pool's minimum is met. [5](#0-4) 

---

### Recommendation

Change both balance checks from `>` (strictly less than required) to `!=` (exact match required):

```solidity
// zeroForOne branch
if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) != balance0()) {
    revert IncorrectDelta();
}

// !zeroForOne branch
if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) != balance1()) {
    revert IncorrectDelta();
}
```

This enforces that the callback pays *exactly* the required amount — no more, no less — matching the invariant that swap output is fully determined by the computed deltas. [6](#0-5) [7](#0-6) 

---

### Proof of Concept

1. Deploy a pool and add liquidity.
2. Deploy a custom callback contract whose `metricOmmSwapCallback` transfers `uint256(amount0Delta) + 1e9` token0 to the pool (instead of exactly `amount0Delta`).
3. Call `pool.swap(recipient, true, amountSpecified, priceLimitX64, "", "")` through the custom contract.
4. Observe:
   - The pool's `balance0()` increases by `amount0Delta + 1e9`.
   - The recipient receives only the pre-computed `amount1Delta` output (unchanged).
   - The extra `1e9` token0 is permanently absorbed by the pool with no corresponding token1 output.
   - No revert occurs because `balance0Before + uint256(amount0Delta) > balance0()` evaluates to `false` (balance is higher, not lower). [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L247-278)
```text
    (int256 amount0Delta, int256 amount1Delta, uint256 protocolFeeAmount) =
      _executeSwap(zeroForOne, amountSpecified, params);

    if (zeroForOne) {
      if (amount1Delta < 0) {
        // casting to uint256 is safe because amount1Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken1(recipient, uint256(-amount1Delta));
      }

      uint256 balance0Before = balance0();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount0Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount0Delta > 0 && balance0Before + uint256(amount0Delta) > balance0()) {
        revert IncorrectDelta();
      }
    } else {
      if (amount0Delta < 0) {
        // casting to uint256 is safe because amount0Delta is negative and the ammount of tokens in pool is capped by uint128.max
        // forge-lint: disable-next-line(unsafe-typecast)
        transferToken0(recipient, uint256(-amount0Delta));
      }

      uint256 balance1Before = balance1();
      IMetricOmmSwapCallback(msg.sender).metricOmmSwapCallback(amount0Delta, amount1Delta, callbackData);
      // casting to uint256 is safe because amount1Delta is positive and the ammount of tokens in pool is capped by uint128.max
      // forge-lint: disable-next-line(unsafe-typecast)
      if (amount1Delta > 0 && balance1Before + uint256(amount1Delta) > balance1()) {
        revert IncorrectDelta();
      }
    }
```

**File:** metric-core/contracts/interfaces/callbacks/IMetricOmmSwapCallback.sol (L1-18)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.35;

/// @title IMetricOmmSwapCallback
/// @notice Callback invoked by the pool during `swap` so the caller can settle token flows with the pool.
/// @dev Implementations must treat `msg.sender` as the pool they intended to call (verify against a known pool).
///      Positive `amount0Delta` / `amount1Delta` mean the pool must receive that many tokens from the callback payer.
///      Negative deltas mean the pool sends tokens out (handled by the pool before this callback for output legs).
///      Both deltas may be zero if no settlement is required for that step.
interface IMetricOmmSwapCallback {
  // ============ Mutating ============

  /// @notice Settle token0 and token1 deltas for the swap on `msg.sender` (the pool).
  /// @param amount0Delta Token0 delta from pool perspective: positive = pool must receive from payer.
  /// @param amount1Delta Token1 delta from pool perspective: positive = pool must receive from payer.
  /// @param callbackData Opaque bytes forwarded from swap; conventionally ABI-encoded router context.
  function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata callbackData) external;
}
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L46-62)
```text
  function metricOmmSwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external override {
    if (amount0Delta <= 0 && amount1Delta <= 0) revert InvalidSwapDeltas();

    _requireExpectedCallbackCaller(msg.sender);

    uint8 callbackMode = _getCallbackMode();

    if (callbackMode == CALLBACK_MODE_JUST_PAY) {
      _justPayCallback(amount0Delta, amount1Delta);
      return;
    }
    if (callbackMode == CALLBACK_MODE_EXACT_OUTPUT_ITERATE) {
      _exactOutputIterateCallback(amount0Delta, amount1Delta, data);
      return;
    }
    revert InvalidCallbackMode(callbackMode);
  }
```
