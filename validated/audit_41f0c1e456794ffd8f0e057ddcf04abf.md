Audit Report

## Title
`SwapAllowlistExtension` checks router address instead of original user, causing complete DoS for all allowlisted users — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary
`SwapAllowlistExtension.beforeSwap` gates swaps by checking `allowedSwapper[pool][sender]`, where `sender` is the `msg.sender` of the `pool.swap()` call. When users route through `MetricOmmSimpleRouter`, `sender` is the router's address, not the original user's address. Since the router is not on the allowlist, every router-mediated swap on an allowlisted pool reverts unconditionally, even for fully allowlisted users.

## Finding Description
`MetricOmmPool.swap()` passes `msg.sender` as the first argument to `_beforeSwap`: [1](#0-0) 

When the call originates from `MetricOmmSimpleRouter.exactInputSingle`, `msg.sender` at the pool is the router contract address, not the original user: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks `allowedSwapper[msg.sender][sender]`, where `msg.sender` = pool and `sender` = router address: [3](#0-2) 

The check becomes `allowedSwapper[pool][router]`, which is `false` unless the pool admin explicitly allowlists the router contract itself. The original user's allowlist entry (`allowedSwapper[pool][user]`) is never consulted, and the call reverts with `NotAllowedToSwap`.

## Impact Explanation
Any pool configured with `SwapAllowlistExtension` becomes completely inaccessible via `MetricOmmSimpleRouter` for all users, regardless of their individual allowlist status. This is a complete DoS of the standard swap path — the primary user-facing interface — for the extension's intended use case. This matches the "Broken core pool functionality causing unusable swap flows" impact category.

## Likelihood Explanation
The `SwapAllowlistExtension` is a production periphery contract explicitly designed to gate pool access by swapper identity. Any pool admin who deploys it with the intent of restricting swappers will immediately and silently break all router-mediated swaps. The failure mode is triggered by normal, expected usage of the router by allowlisted users.

## Recommendation
The extension should check the original initiator of the swap, not the immediate `msg.sender` of `pool.swap()`. Options include:
- Have the pool expose a separate `initiator` field (e.g., via transient storage) that the router sets to `msg.sender` before calling the pool, and the extension reads that field instead of `sender`.
- Alternatively, the pool admin can allowlist the router address and rely on the router's own access control — but this defeats the purpose of per-user allowlisting.

## Proof of Concept
```solidity
function test_allowlistedUserBlockedThroughRouter() public {
    // Pool admin allowlists the user, NOT the router
    swapExtension.setAllowedToSwap(address(pool), user, true);

    // User tries to swap through the router
    vm.prank(user);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(token0),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: user,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // Reverts even though `user` is allowlisted,
    // because the pool sees msg.sender = router, not user.
}
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L37-39)
```text
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
      revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
```
