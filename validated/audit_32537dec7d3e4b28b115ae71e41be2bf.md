The call chain is fully traceable from the code. Here is the analysis:

**Call path:**

1. User calls `router.exactInputSingle(...)` — `msg.sender` = user address
2. Router calls `pool.swap(recipient, zeroForOne, amount, ...)` — router is now `msg.sender` to the pool
3. Pool's `swap` passes `msg.sender` (= router) as `sender` to `_beforeSwap`
4. `_beforeSwap` encodes and calls `extension.beforeSwap(sender=router_address, ...)`
5. `SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router_address]`

The user's address is never present in the extension check. The allowlist check operates on the router's address, not the original user's address.

**Key evidence:**

`MetricOmmPool.swap` passes `msg.sender` (the router) as `sender`: [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[msg.sender][sender]` where `msg.sender` = pool and `sender` = router: [2](#0-1) 

The router sets callback context with `msg.sender` (user) for payment purposes, but this is never forwarded to the pool's `swap` call as the `sender`: [3](#0-2) 

---

### Title
Router address substitution breaks SwapAllowlistExtension per-user gating — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. When a swap is routed through `MetricOmmSimpleRouter`, the pool receives the **router's address** as `msg.sender` and forwards it as `sender` to the extension. The original user's address is never visible to the extension. A pool admin who allowlists specific users but not the router will find that all router-mediated swaps revert with `NotAllowedToSwap`, even for allowlisted users.

### Finding Description
`MetricOmmPool.swap` unconditionally passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // <-- always the immediate caller (router)
    recipient,
    ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

Here `msg.sender` = pool address, `sender` = router address. The user's address is never consulted. A pool admin who calls `setAllowedToSwap(pool, userAddress, true)` expecting to permit a specific user will instead block that user whenever they route through `MetricOmmSimpleRouter`, because `allowedSwapper[pool][routerAddress]` is `false`.

### Impact Explanation
Complete DoS of the router-based swap path for all users of any pool that uses `SwapAllowlistExtension` with a non-open allowlist. Allowlisted users cannot execute swaps through `MetricOmmSimpleRouter`; the only workaround is to call `pool.swap()` directly (bypassing slippage protection, multi-hop routing, and WETH wrapping provided by the router), or for the admin to allowlist the router address (which defeats per-user gating entirely). This breaks core swap functionality as defined in the contest scope.

### Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` with per-user allowlisting (the primary use case of the extension) and expects users to interact via `MetricOmmSimpleRouter` will trigger this. The extension is a production periphery contract explicitly designed for this purpose. The likelihood is high for any pool using this extension with the router.

### Recommendation
The pool should expose the original initiator's address separately from the immediate `msg.sender`, or the router should pass the original user's address through `extensionData` and the extension should decode it. Alternatively, `SwapAllowlistExtension` should document that the allowlist must contain router addresses rather than user addresses when a router is used, and `setAllowedToSwap` should be called with the router address.

### Proof of Concept
1. Deploy pool with `SwapAllowlistExtension`.
2. Pool admin calls `setAllowedToSwap(pool, userAddress, true)` — user is allowlisted, router is not.
3. User calls `router.exactInputSingle(...)`.
4. Router calls `pool.swap(...)` — pool passes `router_address` as `sender` to extension.
5. Extension checks `allowedSwapper[pool][router_address]` → `false` → reverts `NotAllowedToSwap`.
6. User is blocked despite being explicitly allowlisted.

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
