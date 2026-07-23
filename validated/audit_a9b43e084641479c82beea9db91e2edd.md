Audit Report

## Title
Router-mediated swaps bypass `SwapAllowlistExtension` per-user gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is `msg.sender` of the pool's `swap` call — the router address, not the originating user. When a pool admin allowlists the router to permit router-mediated trading, every user, including those explicitly excluded from the allowlist, can bypass the gate by routing through `MetricOmmSimpleRouter`.

## Finding Description
**Root cause:** `MetricOmmPool.swap` passes `msg.sender` (the router) as the `sender` argument to `_beforeSwap`:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← router address, not originating user
    recipient, zeroForOne, amountSpecified,
    priceLimitX64, packedSlot0Initial, bidPriceX64, askPriceX64, extensionData
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to every registered extension. `SwapAllowlistExtension.beforeSwap` checks only that forwarded value:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
```

`msg.sender` here is the pool; `sender` is the router. The check becomes `allowedSwapper[pool][router]`.

In `MetricOmmSimpleRouter.exactInputSingle`, the originating user is stored only in transient storage for the payment callback and is never forwarded to the pool:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., "", params.extensionData
);
```

The same identity loss applies to `exactInput`, `exactOutputSingle`, and `exactOutput` — all router entry points call `pool.swap` with `msg.sender = router`.

**Exploit path:**
1. Pool admin deploys pool with `SwapAllowlistExtension` registered as `beforeSwap` hook.
2. Admin sets `allowedSwapper[pool][alice] = true` (alice is the only permitted swapper).
3. Admin sets `allowedSwapper[pool][router] = true` (required for any router-mediated swap to succeed).
4. Bob (not in allowlist) calls `router.exactInputSingle({pool: pool, ...})`.
5. Router stores bob in transient `T_PAYER_SLOT`, then calls `pool.swap(...)` with `msg.sender = router`.
6. Pool calls `_beforeSwap(sender=router, ...)` → `SwapAllowlistExtension` checks `allowedSwapper[pool][router]` → `true`.
7. Bob's swap proceeds; the allowlist invariant is broken.

**Existing guards are insufficient:** The `onlyPool` modifier on `beforeSwap` (inherited from `BaseMetricExtension`) only verifies the caller is a registered pool — it does not validate the `sender` argument's authenticity. There is no mechanism in the pool or extension interface to distinguish the router's address from the originating user's address.

## Impact Explanation
A pool admin who deploys a pool with `SwapAllowlistExtension` intends to restrict trading to a curated set of addresses. The admin-boundary invariant — that only allowlisted addresses may swap — is completely defeated for any pool that also supports router-mediated trading. Any non-allowlisted user can execute swaps against the pool by routing through the public `MetricOmmSimpleRouter`. This is a direct admin-boundary break: an access-control invariant set by the pool admin is bypassed by an unprivileged path available to any caller.

## Likelihood Explanation
The bypass is unconditional once the router is allowlisted. The router is a public, deployed contract. No special privileges, flash loans, or oracle manipulation are required. The only precondition — the router being allowlisted — is the normal operational state for any pool that intends to support router-mediated trading alongside the allowlist extension. Any user who knows the router address can exploit it.

## Recommendation
The originating user's address must reach `beforeSwap`. Two approaches:

1. **Encode the originating user in `extensionData`**: The router encodes `msg.sender` into `extensionData` before calling `pool.swap`. `SwapAllowlistExtension.beforeSwap` decodes and checks that address instead of (or in addition to) `sender`. This requires a convention between the router and the extension.

2. **Add an originator field to the swap interface**: Extend `IMetricOmmPoolActions.swap` with an explicit `originator` parameter. The router passes `msg.sender` as `originator`; the pool forwards it to `_beforeSwap`; extensions receive it as a distinct argument. This is the structural fix that preserves the identity of the economic actor through the entire call chain.

## Proof of Concept
```
Setup:
  pool = MetricOmmPool with SwapAllowlistExtension registered as beforeSwap hook
  swapExtension.setAllowedToSwap(pool, alice, true)   // alice is the only permitted swapper
  swapExtension.setAllowedToSwap(pool, router, true)  // required for router-mediated swaps
  bob is NOT in allowedSwapper

Attack (Foundry test):
  vm.prank(bob);
  router.exactInputSingle(ExactInputSingleParams({pool: pool, tokenIn: token0, ...}));

Execution trace:
  router stores bob in T_PAYER_SLOT (transient)
  router calls pool.swap(recipient, ...) → msg.sender to pool = router
  pool calls _beforeSwap(sender=router, ...)
  ExtensionCalling encodes sender=router and dispatches to SwapAllowlistExtension
  SwapAllowlistExtension checks allowedSwapper[pool][router] → true
  swap proceeds; bob receives token1 output

Expected: revert NotAllowedToSwap
Actual: swap succeeds — bob bypasses the allowlist
```