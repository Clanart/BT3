The call chain is fully traceable. Here is the analysis:

**Call path:**

1. Unprivileged user calls `MetricOmmSimpleRouter.exactInputSingle(params)` — `msg.sender` = user
2. Router calls `IMetricOmmPoolActions(params.pool).swap(params.recipient, ...)` — `msg.sender` in pool = **router**
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — passes **router address** as `sender`
4. `SwapAllowlistExtension.beforeSwap(sender=router, ...)` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router]`

The check never sees the original user. It sees the router. [1](#0-0) [2](#0-1) [3](#0-2) 

---

### Title
`SwapAllowlistExtension` gates the router intermediary, not the economic actor, allowing any user to bypass the per-user swap allowlist via `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks `allowedSwapper[pool][sender]` where `sender` is the `msg.sender` of `pool.swap()`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the router contract, not the original user. The allowlist therefore gates the router address, not the economic actor.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`:

```solidity
// MetricOmmPool.sol:230-240
_beforeSwap(
  msg.sender,   // <-- whoever called pool.swap(), i.e. the router
  recipient,
  ...
);
```

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
// SwapAllowlistExtension.sol:37
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
```

Here `msg.sender` is the pool and `sender` is the router. The check resolves to `allowedSwapper[pool][router]`.

`MetricOmmSimpleRouter.exactInputSingle` calls `pool.swap()` directly:

```solidity
// MetricOmmSimpleRouter.sol:71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
  .swap(params.recipient, params.zeroForOne, ...);
```

The original `msg.sender` (the user) is stored only in transient callback context for payment purposes — it is never forwarded to the pool as the swap initiator.

Two concrete broken scenarios result:

**Scenario A — Allowlist bypass:** Pool admin allowlists the router address (a natural configuration to permit router-mediated swaps). Any unprivileged user can now call `exactInputSingle` and pass the check, because `sender=router` is allowlisted. The allowlist is completely bypassed for all users.

**Scenario B — Allowlist blocks legitimate users:** Pool admin allowlists individual user addresses. Those users call `exactInputSingle`. The check sees `sender=router`, which is not in the allowlist, and reverts. Allowlisted users cannot use the router at all; they must call `pool.swap()` directly.

Both scenarios represent broken core functionality of the extension.

### Impact Explanation

The `SwapAllowlistExtension` is a production access-control primitive. Its stated purpose is to gate `swap` by swapper address, per pool. Under Scenario A, any unprivileged address can execute swaps on a pool that the admin intended to restrict, directly violating the access-control invariant. Under Scenario B, legitimate allowlisted users are locked out of the standard periphery swap path. Both outcomes constitute broken core pool functionality.

### Likelihood Explanation

The router is the primary public swap entrypoint. Any pool deployer who configures `SwapAllowlistExtension` and expects it to work with the router will encounter this bug. No special attacker capability is required — only calling `exactInputSingle` through the public router.

### Recommendation

The pool should forward the original economic actor's identity, not `msg.sender`, to extension hooks. One approach: add an optional `swapper` parameter to `pool.swap()` that defaults to `msg.sender` but can be set by trusted periphery contracts. Alternatively, `SwapAllowlistExtension` could accept an ABI-encoded `address` in `extensionData` as the identity to check, with the router forwarding `msg.sender` there — but this requires the router to cooperate and is fragile. The cleanest fix is at the pool level: pass the true initiator through to extension hooks.

### Proof of Concept

1. Deploy `MetricOmmPool` with `SwapAllowlistExtension` configured in `beforeSwapOrder`.
2. Pool admin calls `setAllowedToSwap(pool, routerAddress, true)` — allowlisting the router.
3. Unprivileged address `attacker` (not in allowlist) calls `router.exactInputSingle(...)`.
4. Pool calls `_beforeSwap(msg.sender=router, ...)` → extension checks `allowedSwapper[pool][router]` → `true` → swap proceeds.
5. `attacker` successfully swaps on a pool they were never individually authorized to use.

Alternatively, to demonstrate Scenario B:
1. Pool admin calls `setAllowedToSwap(pool, alice, true)` — allowlisting Alice directly.
2. Alice calls `router.exactInputSingle(...)`.
3. Extension checks `allowedSwapper[pool][router]` → `false` → reverts with `NotAllowedToSwap`.
4. Alice cannot use the router despite being explicitly allowlisted. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** metric-core/contracts/MetricOmmPool.sol (L217-240)
```text
  function swap(
    address recipient,
    bool zeroForOne,
    int128 amountSpecified,
    uint128 priceLimitX64,
    bytes calldata callbackData,
    bytes calldata extensionData
  ) external whenNotPaused nonReentrant(PoolActions.SWAP) returns (int128, int128) {
    require(amountSpecified != 0, InvalidAmount());

    uint256 packedSlot0Initial = Slot0Library.loadPackedSlot0();
    (uint128 bidPriceX64, uint128 askPriceX64) = _getBidAndAskPriceX64();

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
