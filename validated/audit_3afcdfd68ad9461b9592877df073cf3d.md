The code confirms the vulnerability. All three key components are verified:

1. `MetricOmmPool.sol` L230-231: `_beforeSwap(msg.sender, ...)` passes the immediate caller (router) as `sender` [1](#0-0) 
2. `SwapAllowlistExtension.sol` L37: checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]` [2](#0-1) 
3. `MetricOmmSimpleRouter.sol` L71-80: router calls `pool.swap(...)` directly, making itself `msg.sender` to the pool [3](#0-2) 

---

Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of original user, causing complete DoS for allowlisted users — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender = msg.sender` of the pool's `swap` call. When a user routes through `MetricOmmSimpleRouter`, that `sender` is the router's address, not the user's address. The allowlist check therefore gates on the router, not the user, causing every router-routed swap to revert with `NotAllowedToSwap` for allowlisted users when the router itself is not allowlisted.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as the `sender` argument to `_beforeSwap` at L230-231. When called via `MetricOmmSimpleRouter.exactInputSingle`, the immediate caller is the router contract, not the original user. `SwapAllowlistExtension.beforeSwap` at L37 evaluates `allowedSwapper[msg.sender][sender]` which resolves to `allowedSwapper[pool][router]`. A pool admin who calls `setAllowedToSwap(pool, alice, true)` but does not allowlist the router will cause all of Alice's router-routed swaps to revert, even though Alice is explicitly allowlisted. The same structural issue applies to `exactInput`, `exactOutputSingle`, and `exactOutput` in the router.

## Impact Explanation
Complete DoS of swap functionality for all allowlisted users routing through `MetricOmmSimpleRouter`. The extension's core purpose — gating swaps by user identity — is silently inverted: the router must be allowlisted instead of the user, which either defeats the allowlist entirely (any user can swap if the router is allowlisted) or blocks all router-routed swaps for legitimate allowlisted users. This constitutes broken core pool swap functionality.

## Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` with per-user allowlisting and expects users to route through `MetricOmmSimpleRouter` is immediately and structurally affected. No special attacker action is required — the mischecked address is a direct consequence of the call chain. This is the primary intended usage of both contracts.

## Recommendation
`SwapAllowlistExtension.beforeSwap` should decode the original user identity from `extensionData` (passed through by the router) and check that address against the allowlist. Alternatively, the router could encode `msg.sender` into `extensionData` before calling `pool.swap`, and the extension would decode and verify it. A third option is adding an explicit `swapper` address to the pool's `swap` interface, though that is a larger protocol change.

## Proof of Concept
1. Deploy pool with `SwapAllowlistExtension` attached.
2. Pool admin calls `setAllowedToSwap(pool, alice, true)` — alice is allowlisted; router is not.
3. Alice calls `router.exactInputSingle(...)`.
4. Router calls `pool.swap(recipient, ...)` — `msg.sender` to pool = router address.
5. Pool calls `extension.beforeSwap(sender=router, ...)`.
6. Extension evaluates `allowedSwapper[pool][router] == false` → reverts `NotAllowedToSwap`.
7. Alice's swap fails despite being explicitly allowlisted.

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L230-231)
```text
    _beforeSwap(
      msg.sender,
```

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-38)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
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
