### Title
`SwapAllowlistExtension` Per-User Gate Is Fully Bypassed When Swapping Through `MetricOmmSimpleRouter` — (File: metric-periphery/contracts/extensions/SwapAllowlistExtension.sol)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. Every router entry-point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()` directly, making the pool see `sender = router`, not the originating user. The extension therefore checks whether the **router** is allowlisted, not whether the **user** is allowlisted. A pool admin who allowlists the router to enable router-mediated swaps for permitted users inadvertently opens the gate to every unprivileged address.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value verbatim to every configured extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap()`: [3](#0-2) 

Every public router function calls `pool.swap()` without forwarding the originating user's address: [4](#0-3) [5](#0-4) 

The pool therefore always sees `sender = router`. The extension checks the router's allowlist status, not the user's.

This creates a structural impossibility for the pool admin:

| Admin configuration | Effect |
|---|---|
| Router **not** allowlisted | All router-mediated swaps revert — even for individually allowlisted users |
| Router **allowlisted** | Every address on-chain can swap through the router, bypassing the per-user gate |

There is no configuration that allows specific users to swap through the router while blocking others.

---

### Impact Explanation

Any pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-gated, institutional, or compliance-restricted pools) loses that restriction entirely for router-mediated swaps. Once the admin allowlists the router — a necessary step for the router to be usable at all — every unprivileged address can execute swaps on the restricted pool. This is an admin-boundary break: an access control invariant set by the pool admin is bypassed by an unprivileged path through the public router.

---

### Likelihood Explanation

High. The `MetricOmmSimpleRouter` is the primary user-facing swap interface. Any pool that uses `SwapAllowlistExtension` and also wants to support router-mediated swaps must allowlist the router, at which point the bypass is immediately available to every address. No special privileges, flash loans, or complex setup are required — a single call to `exactInputSingle` suffices.

---

### Recommendation

Pass the originating user's address through the router to the pool, and have the pool forward it to extensions as a separate `originator` field, or have the router encode the real caller in `callbackData`/`extensionData` and have the extension verify it. Alternatively, `SwapAllowlistExtension.beforeSwap` should check `sender` only when `sender` is not a known router, and fall back to decoding the real caller from `extensionData` when the direct caller is the router. The simplest correct fix is to have the router pass `msg.sender` inside `extensionData` and have the extension decode and check that value instead of the raw `sender`.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  allowedSwapper[pool][alice] = true          // alice is the only permitted user
  allowedSwapper[pool][router] = true         // admin allowlists router so alice can use it

Attack (executed by bob, who is NOT allowlisted):
  bob calls router.exactInputSingle({
      pool:     pool,
      tokenIn:  token0,
      tokenOut: token1,
      ...
  })

Execution trace:
  router.exactInputSingle()
    → pool.swap(recipient=bob, ...)           // msg.sender = router
      → _beforeSwap(sender=router, ...)
        → SwapAllowlistExtension.beforeSwap(sender=router, ...)
          → allowedSwapper[pool][router] == true  ✓  (passes!)
      → swap executes, bob receives token1

Result: bob swaps successfully on a pool that was supposed to block him.
``` [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-240)
```text
    _beforeSwap(
      msg.sender,
      recipient,
      zeroForOne,
      amountSpecified,
      priceLimitX64,
      packedSlot0Initial,
      bidPriceX64,
      askPriceX64,
      extensionData
    );
```

**File:** metric-core/contracts/ExtensionCalling.sol (L149-177)
```text
  function _beforeSwap(
    address sender,
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    uint256 packedSlot0Initial,
    uint128 bidPriceX64,
    uint128 askPriceX64,
    bytes calldata extensionData
  ) internal {
    _callExtensionsInOrder(
      BEFORE_SWAP_ORDER,
      abi.encodeCall(
        IMetricOmmExtensions.beforeSwap,
        (
          sender,
          recipient,
          zeroForOne,
          amountSpecified,
          priceLimitX64,
          packedSlot0Initial,
          bidPriceX64,
          askPriceX64,
          extensionData
        )
      )
    );
  }
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L31-41)
```text
  function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
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
