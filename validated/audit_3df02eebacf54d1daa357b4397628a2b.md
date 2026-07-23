Audit Report

## Title
Router-Mediated Swaps Bypass `SwapAllowlistExtension` Per-User Gate — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` receives `sender = router` for all router-mediated swaps because `MetricOmmPool.swap` passes `msg.sender` (the immediate caller) as `sender` to the extension. If the pool admin allowlists the router address, every unpermissioned user can swap on a restricted pool by routing through `MetricOmmSimpleRouter`, completely defeating the per-user access control invariant.

## Finding Description
`MetricOmmPool.swap` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap` forwards that value unchanged to the extension via `abi.encodeCall`: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` is the pool and `sender` is whoever called `pool.swap` — the router, not the original user: [3](#0-2) 

Meanwhile, `exactInputSingle` stores the original `msg.sender` only in transient callback context for payment purposes and never forwards it to the pool as the swap initiator: [4](#0-3) 

The same pattern applies to `exactInput`, `exactOutputSingle`, and `exactOutput`. [5](#0-4) 

## Impact Explanation
A pool admin who sets `allowAllSwappers[pool] = false` and populates `allowedSwapper[pool]` with individual trusted addresses intends to restrict swaps exclusively to those addresses. If the router is also allowlisted (the natural configuration for any pool that accepts router traffic), the check at line 37 passes for every caller regardless of their individual allowlist status. The allowlist invariant is completely broken for all router-mediated paths, constituting an admin-boundary break where an unprivileged path bypasses pool admin access controls. [6](#0-5) 

## Likelihood Explanation
Any pool using `SwapAllowlistExtension` with `allowAllSwappers = false` and the router in its allowlist is immediately exploitable by any caller. `MetricOmmSimpleRouter` is a public, permissionless contract. No privileged access, special token behavior, or non-standard setup is required — any EOA or contract can call `exactInputSingle`. [7](#0-6) 

## Recommendation
The extension must gate the original user, not the immediate caller of `pool.swap`. The preferred fix is to have `MetricOmmSimpleRouter` encode the original `msg.sender` into `extensionData` using a standardized prefix or dedicated field, and have `SwapAllowlistExtension.beforeSwap` decode and check that value when `sender` is a known router. Alternatively, redesign the pool interface to pass a separate `originator` field that routers populate before calling the pool and that extensions can read alongside `sender`. [8](#0-7) 

## Proof of Concept
```solidity
// Setup
pool.setExtension(swapAllowlistExtension);
swapAllowlistExtension.setAllowAllSwappers(pool, false);
swapAllowlistExtension.setAllowedToSwap(pool, address(router), true);  // router allowed
swapAllowlistExtension.setAllowedToSwap(pool, address(user), false);   // user NOT allowed

// Attack: user routes through router
// pool.swap is called with msg.sender = router
// _beforeSwap receives sender = router
// allowedSwapper[pool][router] = true → check passes
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L12-13)
```text
  mapping(address pool => mapping(address swapper => bool)) public allowedSwapper;
  mapping(address pool => bool) public allowAllSwappers;
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L103-112)
```text
      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
      (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
        .swap(
          i == last ? params.recipient : address(this),
          zeroForOne,
          amount,
          MetricOmmSwapPath.openLimit(zeroForOne),
          "",
          params.extensionDatas[i]
        );
```
