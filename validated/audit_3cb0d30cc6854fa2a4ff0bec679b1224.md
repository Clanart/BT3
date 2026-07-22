The code path is fully traceable. Here is the determination:

**Call chain:**

1. Non-allowlisted user calls `router.exactInputSingle()` [1](#0-0) 
2. Router calls `pool.swap(...)` — the router is now `msg.sender` of the pool call [2](#0-1) 
3. Pool calls `_beforeSwap(msg.sender, ...)` — passing the **router address** as `sender` [3](#0-2) 
4. `ExtensionCalling._beforeSwap` forwards `sender` (= router) to the extension [4](#0-3) 
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]` [5](#0-4) 

**The bypass is real, but the framing in the question is wrong in one key detail:** `CALLBACK_MODE_JUST_PAY` is irrelevant. That mode governs the payment callback that fires *after* `beforeSwap` has already passed. The allowlist check happens before the callback, and the bypass requires nothing special about the callback mode.

**The actual vulnerability:** When a pool admin allowlists the router address (a necessary step for any allowlisted user to use the router), `allowedSwapper[pool][router] = true` causes the extension to pass for *every* caller of the router, not just the intended allowlisted users. The extension has no way to simultaneously allow specific users through the router while blocking others, because it only sees the router's address as `sender`.

**Is this a real finding?** Yes, with the following precise conditions:

- Pool admin sets `allowAllSwappers[pool] = false` (restricted mode)
- Pool admin calls `setAllowedToSwap(pool, router, true)` to allow their allowlisted users to use the router
- Any non-allowlisted user calls `router.exactInputSingle(pool, ...)` and the `beforeSwap` check passes because `allowedSwapper[pool][router]` is true

The pool admin's action of allowlisting the router is a reasonable and expected operational step — it is not a "malicious pool setup." The design flaw is that `SwapAllowlistExtension` cannot enforce per-user identity through an intermediary router.

---

### Title
`SwapAllowlistExtension` gates on the router address instead of the originating user, allowing any caller to bypass per-user swap restrictions on pools that allowlist the router — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the pool's `msg.sender`. When swaps are routed through `MetricOmmSimpleRouter`, `sender` is always the router address, not the originating user. A pool admin who allowlists the router to enable legitimate users to swap through it inadvertently opens the pool to every caller of the router.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`, which forwards it to all configured extensions. [6](#0-5) 

`SwapAllowlistExtension.beforeSwap` uses that `sender` value — the immediate pool caller — to look up the allowlist: [5](#0-4) 

`MetricOmmSimpleRouter.exactInputSingle` (and all other `exact*` entry points) calls `pool.swap` directly, making the router the pool's `msg.sender`: [7](#0-6) 

The original user's address is stored only in transient storage as the payer, never surfaced to the extension. There is no mechanism in the extension interface for the router to forward the originating user's identity.

### Impact Explanation
Any user can execute swaps on a pool configured with `allowAllSwappers=false` by routing through `MetricOmmSimpleRouter`, provided the router is allowlisted. The `SwapAllowlistExtension` is designed to restrict swap access to specific addresses (e.g., KYC-gated or institutional pools). This bypass renders that restriction unenforceable for router-mediated swaps, which is the primary intended user path.

### Likelihood Explanation
The pool admin must allowlist the router for any allowlisted user to use it. This is a standard operational step. Once done, the bypass is available to any address with no further preconditions.

### Recommendation
The router should forward the originating user's address to the pool via the `extensionData` field, and `SwapAllowlistExtension` should decode and verify it. Alternatively, the pool interface should support a separate `originator` argument that the router populates with `msg.sender` before calling `pool.swap`, and the extension should gate on that value instead of `sender`.

### Proof of Concept
1. Deploy a pool with `SwapAllowlistExtension`, set `allowAllSwappers[pool] = false`.
2. Call `setAllowedToSwap(pool, router, true)` — allowlist the router so legitimate users can use it.
3. From an address `attacker` not in `allowedSwapper`, call `router.exactInputSingle({pool: pool, ...})`.
4. The pool calls `extension.beforeSwap(sender=router, ...)`. The check `allowedSwapper[pool][router]` is `true` → passes.
5. The swap executes and `attacker`'s ERC20 balance decreases, confirming the bypass.

### Citations

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

**File:** metric-core/contracts/ExtensionCalling.sol (L160-176)
```text
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
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
