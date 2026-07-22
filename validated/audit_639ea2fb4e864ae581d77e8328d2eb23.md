The call chain is fully traceable and confirms the vulnerability is real. Here is the complete analysis:

**Call chain:**

1. User calls `MetricOmmSimpleRouter.exactInputSingle(params)` — `msg.sender` = user address
2. Router calls `IMetricOmmPoolActions(params.pool).swap(...)` — the router is now `msg.sender` of this call
3. `MetricOmmPool.swap()` calls `_beforeSwap(msg.sender, ...)` — `msg.sender` here is the **router address**
4. `ExtensionCalling._beforeSwap()` encodes and forwards `sender = router_address` to `extension.beforeSwap(sender, ...)`
5. `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router_address]`

The pool admin allowlisted the user's EOA address, not the router. The check fails. The swap reverts with `NotAllowedToSwap` even though the user is allowlisted.

---

### Title
`SwapAllowlistExtension.beforeSwap` checks the router's address instead of the original user's address, causing complete DoS for all allowlisted users routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary

`SwapAllowlistExtension` is designed to gate pool swaps by individual swapper address. However, when a user routes through `MetricOmmSimpleRouter`, the pool receives the **router contract** as `msg.sender` and passes it as `sender` to `beforeSwap`. The allowlist check therefore tests whether the **router** is allowlisted, not the original user, silently breaking per-user access control.

### Finding Description

`MetricOmmPool.swap()` passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

`ExtensionCalling._beforeSwap()` forwards that value unchanged to the extension: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap()` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is the **router address** (wrong — it should be the original user). The pool admin allowlisted the user's EOA via `setAllowedToSwap(pool, user, true)`, so `allowedSwapper[pool][router]` is `false` and the call reverts.

The router sets callback context with `msg.sender` as the payer but has no mechanism to forward the original caller's identity to the pool's `swap()` call: [4](#0-3) 

### Impact Explanation

Complete DoS of swap functionality for every allowlisted user who routes through `MetricOmmSimpleRouter`. The only workaround is for users to call `pool.swap()` directly (bypassing slippage protection, multi-hop routing, ETH wrapping, and permit flows provided by the router) or for the pool admin to allowlist the router address — which defeats the purpose of per-user access control entirely, since it grants swap access to all router users indiscriminately.

This breaks core swap functionality for the intended use case of the extension.

### Likelihood Explanation

Any pool that deploys `SwapAllowlistExtension` to restrict swaps to specific addresses and expects users to interact via the standard router will be affected. This is the primary intended use case of the extension. The condition is triggered on every `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` call through the router for any allowlisted user.

### Recommendation

The extension should check the `recipient` parameter (the intended beneficiary of the swap) rather than `sender`, or the router should pass the original `msg.sender` through `extensionData` and the extension should decode it. The cleanest fix is to check `recipient` in `beforeSwap`, since the pool already passes it as the second argument:

```solidity
function beforeSwap(address sender, address recipient, bool, int128, uint128, uint256, uint128, uint128, bytes calldata)
    external view override returns (bytes4)
{
    address swapper = recipient != address(0) ? recipient : sender;
    if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][swapper]) {
        revert IMetricOmmPoolActions.NotAllowedToSwap();
    }
    return IMetricOmmExtensions.beforeSwap.selector;
}
```

Alternatively, the router could encode the original `msg.sender` into `extensionData` and the extension could decode it, but this requires coordination between router and extension.

### Proof of Concept

```solidity
// Foundry integration test
function test_allowlistExtension_dosViaRouter() public {
    // Pool admin allowlists user but NOT the router
    swapAllowlistExtension.setAllowedToSwap(address(pool), user, true);
    // router address is NOT allowlisted

    vm.startPrank(user);
    token0.approve(address(router), type(uint256).max);

    // User routes through MetricOmmSimpleRouter
    // pool.swap() receives msg.sender = router_address
    // beforeSwap checks allowedSwapper[pool][router_address] => false
    vm.expectRevert(IMetricOmmPoolActions.NotAllowedToSwap.selector);
    router.exactInputSingle(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: address(pool),
            tokenIn: address(token0),
            recipient: user,
            deadline: block.timestamp + 1,
            amountIn: 1000,
            amountOutMinimum: 0,
            zeroForOne: true,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    vm.stopPrank();
    // User is allowlisted but cannot swap through the router — complete DoS
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
