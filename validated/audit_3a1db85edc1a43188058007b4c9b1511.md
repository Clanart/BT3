Looking at the pool's `swap()` function, the extension calling chain, and the `SwapAllowlistExtension`, I can trace the exact identity that the allowlist gate checks when a user routes through `MetricOmmSimpleRouter`.

**Key trace:**

1. `MetricOmmPool.swap()` passes `msg.sender` as `sender` to `_beforeSwap()` — when called via the router, `msg.sender` = router address.
2. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` where `sender` = router address.
3. `DepositAllowlistExtension.beforeAddLiquidity()` checks `allowedDepositor[msg.sender][owner]` where `owner` = actual position owner — correctly gating the beneficiary.

The swap allowlist checks the wrong identity (router) while the deposit allowlist correctly checks the beneficiary (`owner`). This is the direct analog to the external bug's "wrong role checked" pattern.

---

### Title
Non-allowlisted users bypass `SwapAllowlistExtension` via `MetricOmmSimpleRouter` because the extension gates the router address, not the actual swapper — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` checks the `sender` argument, which the pool sets to `msg.sender` of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, `msg.sender` is the router contract, not the end user. A pool admin who allowlists the router to enable router-mediated swaps inadvertently opens the allowlist to every user on-chain, completely defeating the access-control invariant the extension is meant to enforce.

### Finding Description

`MetricOmmPool.swap()` passes its own `msg.sender` as the `sender` argument to every extension hook:

```solidity
// metric-core/contracts/MetricOmmPool.sol  line 230-240
_beforeSwap(
    msg.sender,   // ← router address when called via MetricOmmSimpleRouter
    recipient,
    zeroForOne,
    amountSpecified,
    priceLimitX64,
    packedSlot0Initial,
    bidPriceX64,
    askPriceX64,
    extensionData
);
``` [1](#0-0) 

`SwapAllowlistExtension.beforeSwap` then gates on that `sender`:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol  line 37-39
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [2](#0-1) 

When `MetricOmmSimpleRouter.exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) calls `pool.swap()`, the pool's `msg.sender` is the router contract:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol  line 72-80
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(
        params.recipient,
        params.zeroForOne,
        MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn),
        priceLimitX64,
        "",
        params.extensionData
    );
``` [3](#0-2) 

The extension therefore evaluates `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`. The pool admin faces an impossible choice:

| Admin action | Effect |
|---|---|
| Do **not** allowlist the router | Router-mediated swaps revert for **everyone**, including legitimately allowlisted users |
| Allowlist the router | **Every** user on-chain can bypass the allowlist by routing through the router |

By contrast, `DepositAllowlistExtension.beforeAddLiquidity` correctly gates on `owner` (the actual position beneficiary), not `sender` (the liquidity adder contract):

```solidity
// metric-periphery/contracts/extensions/DepositAllowlistExtension.sol  line 38-40
if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
    revert IMetricOmmPoolActions.NotAllowedToDeposit();
}
``` [4](#0-3) 

The asymmetry confirms the swap-side check is the defective one.

### Impact Explanation
Any pool that deploys `SwapAllowlistExtension` to restrict trading to a curated set of addresses (e.g., KYC-verified counterparties, institutional market-makers, or protocol-controlled addresses) loses that restriction entirely for router-mediated swaps. Unauthorized users can extract value from LPs who deposited under the assumption that only allowlisted counterparties would trade against their positions. Because the router is the canonical, gas-efficient entry point for end users, the bypass is trivially reachable by any on-chain actor.

### Likelihood Explanation
High. The router is the primary user-facing entry point for swaps. Any pool that uses `SwapAllowlistExtension` and also wants to support router-mediated swaps must allowlist the router, at which point the allowlist is fully open. The protocol's own audit target list explicitly flags this path: *"Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract."* [5](#0-4) 

### Recommendation
The extension must gate on the **economic actor** (the end user), not the **call-chain intermediary** (the router). Two viable approaches:

1. **Pass the original caller through `extensionData`**: The router encodes `msg.sender` (the user) into `extensionData` for each hop, and `SwapAllowlistExtension` decodes and checks it. This requires a convention between the router and the extension.

2. **Check `recipient` instead of `sender`**: For single-hop swaps where the user is also the recipient, gating on `recipient` would correctly identify the beneficiary. This breaks for multi-hop paths where intermediate recipients are the router itself.

3. **Dedicated router-aware allowlist**: Introduce a separate mapping `allowedSwapperForRouter[pool][user]` and have the router pass the user address in a standardized `extensionData` field that the extension reads.

The deposit-side design (`owner` is the gated identity) is the correct model; the swap-side should mirror it by gating on the economically relevant actor rather than the call-chain intermediary.

### Proof of Concept

```
Setup:
  - Pool P with SwapAllowlistExtension E configured
  - allowedSwapper[P][alice] = true   (alice is the only allowed swapper)
  - allowedSwapper[P][router] = true  (admin adds router to enable router-mediated swaps)

Attack (bob, not allowlisted):
  1. bob calls MetricOmmSimpleRouter.exactInputSingle({pool: P, ...})
  2. Router calls P.swap(recipient=bob, ...)
  3. Pool calls _beforeSwap(msg.sender=router, ...)
  4. SwapAllowlistExtension checks allowedSwapper[P][router] → true
  5. Swap execeds successfully for bob despite bob not being on the allowlist

Result:
  - bob swaps on a pool intended to be restricted to alice only
  - LPs are exposed to an unauthorized counterparty
  - The allowlist invariant is broken
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L72-80)
```text
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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L38-40)
```text
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
```

**File:** generate_scanned_questions.py (L655-663)
```python
        Target(
            short="swap allowlist gate",
            file_function="metric-periphery/contracts/extensions/SwapAllowlistExtension.sol::beforeSwap",
            entrypoint="metric-core/contracts/MetricOmmPool.sol::swap and metric-periphery/contracts/MetricOmmSimpleRouter.sol::exact*",
            call_path="public swap -> beforeSwap hook -> allowAll/allowedSwapper lookup keyed by pool and sender",
            values="the exact swapper identity checked by the hook and whether router-mediated swaps preserve that identity",
            control_hint="Because public users may enter through the router, the hook must gate the same actor the pool designers thought they were allowlisting.",
            validation_focus="Test direct swaps and router swaps on allowlisted pools and assert the hook cannot be bypassed by routing through an intermediate public contract.",
        ),
```
