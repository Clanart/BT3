The call chain is fully traceable. Here is the verdict:

---

### Title
Router Address Substitution in `SwapAllowlistExtension.beforeSwap` Causes Complete DoS for Allowlisted Users — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension.beforeSwap` checks the `sender` argument against the per-pool allowlist. The pool passes `msg.sender` of the `swap` call as `sender`. When a user routes through `MetricOmmSimpleRouter`, the pool's `msg.sender` is the **router address**, not the original user. The allowlist check therefore tests whether the **router** is allowlisted, not the user, silently blocking every allowlisted user who routes through the router.

### Finding Description

**Step 1 — Router calls pool.swap with itself as msg.sender**

In `MetricOmmSimpleRouter.exactInputSingle`:

```solidity
_setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
IMetricOmmPoolActions(params.pool).swap(
    params.recipient, params.zeroForOne, ..., params.extensionData
);
``` [1](#0-0) 

The router stores the original `msg.sender` (user) only as the **payer** in transient context. The pool call itself is made by the router, so `msg.sender` inside the pool is the router address.

**Step 2 — Pool passes msg.sender as `sender` to _beforeSwap**

```solidity
_beforeSwap(
    msg.sender,   // <-- router address, not the original user
    recipient, ...
);
``` [2](#0-1) 

**Step 3 — Extension checks the router address against the allowlist**

```solidity
function beforeSwap(address sender, ...) external view override returns (bytes4) {
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    ...
}
``` [3](#0-2) 

Here `msg.sender` = pool, `sender` = router address. The check is `allowedSwapper[pool][router]`, not `allowedSwapper[pool][user]`.

### Impact Explanation

- If the pool admin allowlists individual users but not the router, **every allowlisted user is blocked** when routing through `MetricOmmSimpleRouter`.
- The only workaround is to allowlist the router address itself, but that grants swap access to **all users** routing through the router, completely defeating the purpose of the per-user allowlist.
- This is broken core swap functionality: the extension's stated invariant ("gates `swap` by swapper address, per pool") is violated for all router-mediated swaps.

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` with per-user allowlisting and expects users to route through `MetricOmmSimpleRouter` will be affected immediately. No special attacker action is required — the miscorrelation is structural and triggered by normal usage.

### Recommendation

The pool should pass the original initiator address separately from the immediate caller. One approach: add a dedicated `swapInitiator` parameter to `pool.swap` (or encode it in `callbackData`/`extensionData`) so the extension can check the true originating user. Alternatively, `SwapAllowlistExtension` could accept a user address from `extensionData` with a signature, but that requires off-chain coordination. The cleanest fix is for the pool to expose the original initiator to extensions.

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_allowlistedUserBlockedThroughRouter() public {
    // Pool admin allowlists `user` but NOT the router
    vm.prank(poolAdmin);
    swapAllowlist.setAllowedToSwap(address(pool), user, true);

    // user tries to swap through the router
    vm.prank(user);
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    router.exactInputSingle(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: tokenIn,
        tokenOut: tokenOut,
        zeroForOne: true,
        amountIn: 1000,
        amountOutMinimum: 0,
        recipient: user,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // Reverts because allowedSwapper[pool][router] == false,
    // even though allowedSwapper[pool][user] == true
}
```

### Citations

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
