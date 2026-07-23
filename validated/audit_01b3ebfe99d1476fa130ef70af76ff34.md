Audit Report

## Title
`SwapAllowlistExtension.beforeSwap` checks router address as swapper instead of original user, causing DoS for all allowlisted users routing through `MetricOmmSimpleRouter` — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

## Summary

`SwapAllowlistExtension` is designed to gate pool swaps by individual user address. When a user routes through `MetricOmmSimpleRouter`, `MetricOmmPool.swap()` receives the router contract as `msg.sender` and passes it as `sender` to `_beforeSwap`. The extension then checks `allowedSwapper[pool][router_address]`, not `allowedSwapper[pool][user_address]`, causing every allowlisted user's swap to revert with `NotAllowedToSwap` when using the router.

## Finding Description

The call chain is confirmed by production code:

**Step 1:** `MetricOmmPool.swap()` passes `msg.sender` (the router) as `sender` to `_beforeSwap`: [1](#0-0) 

**Step 2:** `ExtensionCalling._beforeSwap()` forwards `sender` (router address) unchanged to the extension: [2](#0-1) 

**Step 3:** `SwapAllowlistExtension.beforeSwap()` checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router_address]`: [3](#0-2) 

**Step 4:** The router has no mechanism to encode the original `msg.sender` into the pool's `swap()` call. It passes `params.extensionData` directly without injecting the original caller: [4](#0-3) 

The pool admin calls `setAllowedToSwap(pool, user, true)`, setting `allowedSwapper[pool][user] = true`. But the check evaluates `allowedSwapper[pool][router]`, which is `false`, so the call reverts. No existing guard compensates for this mismatch. [5](#0-4) 

## Impact Explanation

Complete DoS of the swap flow for every allowlisted user who routes through `MetricOmmSimpleRouter`. This breaks core swap functionality: the only workaround is to call `pool.swap()` directly, which bypasses the router's slippage protection (`amountOutMinimum`/`amountInMaximum`), multi-hop routing, ETH wrapping, and permit flows. Alternatively, the pool admin can allowlist the router address, which defeats per-user access control entirely by granting swap access to all router users indiscriminately. This matches the allowed impact: "Broken core pool functionality causing unusable swap flows."

## Likelihood Explanation

Any pool deploying `SwapAllowlistExtension` to restrict swaps to specific addresses — the primary intended use case — is affected on every `exactInputSingle`, `exactInput`, `exactOutputSingle`, or `exactOutput` call through the router. No special attacker capability is required; the condition is triggered automatically by any allowlisted user using the standard router.

## Recommendation

The extension should check `recipient` (the intended beneficiary, already passed as the second argument) rather than `sender`, since `recipient` reflects the user's address set by the router:

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

Alternatively, the router could encode the original `msg.sender` into `extensionData` and the extension could decode it, but this requires coordination between router and extension deployments.

## Proof of Concept

```solidity
// Foundry integration test
function test_allowlistExtension_dosViaRouter() public {
    // Pool admin allowlists user EOA but NOT the router
    swapAllowlistExtension.setAllowedToSwap(address(pool), user, true);
    // router address is NOT allowlisted

    vm.startPrank(user);
    token0.approve(address(router), type(uint256).max);

    // User routes through MetricOmmSimpleRouter
    // pool.swap() receives msg.sender = router_address
    // _beforeSwap forwards sender = router_address to extension
    // beforeSwap checks allowedSwapper[pool][router_address] => false => revert
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

**File:** metric-periphery/contracts/extensions/SwapAllowlistExtension.sol (L17-19)
```text
  function setAllowedToSwap(address pool_, address swapper, bool allowed) external onlyPoolAdmin(pool_) {
    allowedSwapper[pool_][swapper] = allowed;
    emit AllowedToSwapSet(pool_, swapper, allowed);
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
