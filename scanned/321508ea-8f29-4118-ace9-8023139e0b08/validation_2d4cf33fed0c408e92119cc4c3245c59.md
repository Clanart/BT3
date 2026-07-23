The call chain is fully traceable and the vulnerability is real. Here is the complete analysis:

**Call flow:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)` — `msg.sender = user`
2. Router calls `IMetricOmmPoolActions(params.pool).swap(...)` — `msg.sender` to the pool is the **router address**
3. `MetricOmmPool.swap` calls `_beforeSwap(msg.sender, ...)` — passes the **router address** as `sender`
4. `ExtensionCalling._beforeSwap` encodes and dispatches `sender` (router) to the extension
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `sender = router`

The check at line 37 of `SwapAllowlistExtension` never sees the original user — it only ever sees whoever called `pool.swap`, which is the router.

---

### Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` Per-User Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender = router` for all router-mediated swaps. If the pool admin allowlists the router (a natural configuration for a pool that accepts router traffic), every unpermissioned user can swap on a restricted pool by routing through `MetricOmmSimpleRouter`.

### Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[pool][sender]`, where `sender` is the router, not the original user: [3](#0-2) 

Meanwhile, `exactInputSingle` sets the callback context with `msg.sender` (the user) only for payment purposes — it is never forwarded to the pool as the swap initiator: [4](#0-3) 

### Impact Explanation
A pool admin who configures `allowAllSwappers = false` and adds individual trusted addresses to `allowedSwapper` intends to restrict swaps to those addresses only. If the router is also allowlisted (the natural setup for a pool that accepts router traffic), **any address** can bypass the per-user gate by calling `exactInputSingle` / `exactInput` / `exactOutputSingle` / `exactOutput` through the router. The allowlist invariant is completely broken for router-mediated paths.

### Likelihood Explanation
Any pool using `SwapAllowlistExtension` with `allowAllSwappers = false` and the router in its allowlist is immediately exploitable by any caller. The router is a public, permissionless contract. No privileged access, malicious setup, or non-standard token is required.

### Recommendation
The extension must gate the **original user**, not the immediate caller of `pool.swap`. Two options:

1. **Pass the original initiator through the pool**: Have the router encode the original `msg.sender` in `extensionData` and have the extension decode it — but this is fragile and trust-dependent.
2. **Preferred — check `recipient` or use a dedicated field**: Redesign the extension interface so the pool passes a separate `originator` field that the router populates with `msg.sender` before calling the pool, and the pool forwards it to extensions alongside `sender`.
3. **Simplest mitigation**: Document that `SwapAllowlistExtension` cannot be used with the router, and add a guard in the extension that reverts if `sender` is a known router address — though this is incomplete.

The cleanest fix is to have `MetricOmmSimpleRouter` pass the original `msg.sender` in a standardized field that `beforeSwap` can read, rather than relying on the pool's `msg.sender`.

### Proof of Concept
```
// Setup
pool.setExtension(swapAllowlistExtension);
swapAllowlistExtension.setAllowAllSwappers(pool, false);
swapAllowlistExtension.setAllowedToSwap(pool, address(router), true);   // router is allowed
swapAllowlistExtension.setAllowedToSwap(pool, address(user), false);    // user is NOT allowed

// Attack: user routes through router
// beforeSwap receives sender=router → allowedSwapper[pool][router]=true → passes
router.exactInputSingle(ExactInputSingleParams({pool: pool, ..., recipient: user, ...}));
// Swap succeeds despite user not being in the allowlist
```

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-80)
```text
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
```
