### Title
`SwapAllowlistExtension` Enforces Allowlist on Router Address Instead of Originating EOA, Enabling Full Bypass — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

---

### Summary

`SwapAllowlistExtension.beforeSwap` gates swaps by checking the `sender` argument passed by the pool. When users route through `MetricOmmSimpleRouter`, `sender` is the **router contract address**, not the originating EOA. If the pool admin allowlists the router (the only way to enable router-mediated swaps), every user — including those individually blocked — can bypass the per-user allowlist by routing through the router.

---

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to every configured extension:

```solidity
// metric-core/contracts/MetricOmmPool.sol:230-240
_beforeSwap(
    msg.sender,   // ← direct caller of pool.swap()
    recipient,
    ...
);
```

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension:

```solidity
// metric-core/contracts/ExtensionCalling.sol:160-176
_callExtensionsInOrder(
    BEFORE_SWAP_ORDER,
    abi.encodeCall(IMetricOmmExtensions.beforeSwap, (sender, recipient, ...))
);
```

`SwapAllowlistExtension.beforeSwap` then checks that `sender` against the per-pool allowlist:

```solidity
// metric-periphery/contracts/extensions/SwapAllowlistExtension.sol:31-41
function beforeSwap(address sender, address, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

When a user calls `MetricOmmSimpleRouter.exactInputSingle()`, the router calls `pool.swap()` directly:

```solidity
// metric-periphery/contracts/MetricOmmSimpleRouter.sol:71-80
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
(int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
    .swap(params.recipient, params.zeroForOne, ...);
```

The pool's `msg.sender` is the **router**, so `sender = router` reaches the extension. The extension checks `allowedSwapper[pool][router]`, not `allowedSwapper[pool][EOA]`.

The same substitution occurs in `exactInput` (multi-hop, first hop: `i == 0 ? msg.sender : address(this)` stores the EOA as the *payer* in transient storage, but the pool still sees the router as `msg.sender`) and in `exactOutput` / `exactOutputSingle`.

**Contrast with `DepositAllowlistExtension`:** that extension correctly ignores the `sender` parameter (first `address,` is unnamed/discarded) and gates on `owner` — the position owner — which the liquidity adder passes explicitly and which the pool records in state. The swap extension has no equivalent "real user" field to check; it only receives `sender` = the direct pool caller.

---

### Impact Explanation

Two failure modes, both breaking the allowlist invariant:

1. **Bypass (primary impact):** Pool admin allowlists the router address so that users can swap through it. Every user — including those individually blocked — can now call `router.exactInputSingle()` and pass the allowlist check, because the check resolves to `allowedSwapper[pool][router] == true`. The per-user allowlist is completely nullified for all router-mediated swaps.

2. **Lockout (secondary impact):** Pool admin does not allowlist the router. Individually allowlisted users cannot use the router at all; they must call `pool.swap()` directly, losing slippage protection, multi-hop routing, WETH wrapping, and permit support.

In the bypass scenario, the pool admin's intended access control — restricting which addresses may trade — is silently circumvented for the entire router user base. Any user the admin intended to block (e.g., a sanctioned address, a known manipulator) can continue swapping through the router without restriction.

---

### Likelihood Explanation

- The `SwapAllowlistExtension` is a production periphery contract, not a test mock.
- The `MetricOmmSimpleRouter` is the primary user-facing swap entry point.
- A pool admin who deploys a pool with `SwapAllowlistExtension` and wants users to use the router **must** allowlist the router, triggering the bypass automatically.
- No special privileges, flash loans, or unusual token behavior are required. Any EOA can call `router.exactInputSingle()`.

---

### Recommendation

Pass the originating EOA through the swap path so the extension can gate on the real user. Two options:

**Option A (preferred):** Add a `swapper` field to the swap call or extension context that the router populates with `msg.sender` before calling the pool. The pool forwards this field to extensions alongside `sender`.

**Option B:** Mirror the `DepositAllowlistExtension` pattern — gate on `recipient` (the address receiving output tokens) rather than `sender`. This is imperfect (recipient can be a third party) but is closer to the real user than the router address.

Either way, the extension must not gate on the direct pool caller (`sender`) when an intermediary router is in the call chain.

---

### Proof of Concept

```
Setup:
  pool configured with SwapAllowlistExtension
  admin calls setAllowedToSwap(pool, router, true)   // router allowlisted so users can swap
  admin calls setAllowedToSwap(pool, alice, false)   // alice individually blocked

Attack:
  alice calls router.exactInputSingle({pool, tokenIn, tokenOut, ...})
    → router calls pool.swap(recipient=alice, ...)
    → pool calls _beforeSwap(msg.sender=router, ...)
    → extension checks allowedSwapper[pool][router] == true  ✓ passes
    → swap executes; alice receives output tokens

Result:
  alice, who should be blocked, successfully swaps through the router.
  The per-user allowlist is completely bypassed.
```

**Relevant code locations:**

- Extension check: [1](#0-0) 
- Pool passes `msg.sender` as `sender`: [2](#0-1) 
- Router calls `pool.swap()` directly (router is `msg.sender` of pool): [3](#0-2) 
- Extension forwarding in `_beforeSwap`: [4](#0-3) 
- Contrast: deposit allowlist correctly gates on `owner`, not `sender`: [5](#0-4)

### Citations

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

**File:** metric-periphery/contracts/extensions/DepositAllowlistExtension.sol (L32-42)
```text
  function beforeAddLiquidity(address, address owner, uint80, LiquidityDelta calldata, bytes calldata)
    external
    view
    override
    returns (bytes4)
  {
    if (!allowAllDepositors[msg.sender] && !allowedDepositor[msg.sender][owner]) {
      revert IMetricOmmPoolActions.NotAllowedToDeposit();
    }
    return IMetricOmmExtensions.beforeAddLiquidity.selector;
  }
```
