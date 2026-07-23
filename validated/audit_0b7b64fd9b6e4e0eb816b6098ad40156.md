The call chain is fully traceable from the code. Here is the analysis:

**Call trace:**

1. User calls `router.exactInputSingle(...)` — `msg.sender` = user address
2. Router calls `pool.swap(recipient, zeroForOne, amount, ...)` — `msg.sender` to the pool = **router address**
3. Pool's `swap()` calls `_beforeSwap(msg.sender, ...)` at line 231 — `msg.sender` is the **router address**
4. Extension receives `sender = router_address`
5. Extension checks `allowedSwapper[msg.sender][sender]` = `allowedSwapper[pool][router_address]`

The `sender` forwarded to `beforeSwap` is always the direct caller of `pool.swap()`, which is the router, not the original user.

---

### Title
`SwapAllowlistExtension` checks router address instead of original user, causing DoS for allowlisted users and allowlist bypass if router is whitelisted — (`metric-periphery/contracts/extensions/SwapAllowlistExtension.sol`)

### Summary
`SwapAllowlistExtension.beforeSwap` receives `sender` as the direct caller of `pool.swap()`. When swaps are routed through `MetricOmmSimpleRouter`, that caller is the router contract, not the end user. The allowlist check therefore operates on the router's address, not the user's address, breaking the extension's core invariant in both directions.

### Finding Description
In `MetricOmmPool.swap()`, the pool passes `msg.sender` as the `sender` argument to `_beforeSwap`: [1](#0-0) 

When the router calls `pool.swap(...)`, `msg.sender` inside the pool is the router address: [2](#0-1) 

`SwapAllowlistExtension.beforeSwap` then checks:

```solidity
if (!allowAllSwappers[msg.sender] && !allowedSwapper[msg.sender][sender]) {
    revert IMetricOmmPoolActions.NotAllowedToSwap();
}
``` [3](#0-2) 

Here `msg.sender` is the pool (correct) and `sender` is the router address (incorrect — should be the end user). The allowlist maps `pool => swapper => bool`, but the swapper being checked is the router, not the user.

### Impact Explanation
Two broken invariants result:

1. **DoS for allowlisted users**: If the pool admin allowlists individual users but not the router, every swap through `MetricOmmSimpleRouter` reverts with `NotAllowedToSwap`, even for allowlisted users. The extension is completely non-functional for router-mediated swaps.

2. **Allowlist bypass**: If the pool admin allowlists the router to fix (1), every user — including non-allowlisted ones — can swap through the router, defeating the purpose of the extension entirely.

Both outcomes break the core functionality of the `SwapAllowlistExtension` and constitute a broken swap flow for all users of pools configured with this extension.

### Likelihood Explanation
Any pool that deploys `SwapAllowlistExtension` and expects users to interact via `MetricOmmSimpleRouter` (the canonical periphery router) will be affected. The pool admin has no on-chain mechanism to pass the original user's address through the router — the router does not forward `msg.sender` as `extensionData` or any other parameter. The condition is triggered by normal, expected usage.

### Recommendation
The router should forward the original `msg.sender` (the end user) in `extensionData`, and `SwapAllowlistExtension` should decode and use that address when the caller is a known/trusted router. Alternatively, the pool's `swap()` interface could accept an explicit `originator` parameter that the router populates with `msg.sender`, and extensions would use that for allowlist checks. A simpler mitigation is to document that `SwapAllowlistExtension` is incompatible with router-mediated swaps and gate it at the pool level.

### Proof of Concept
```solidity
// Pool admin allowlists `user` but not `router`
extension.setAllowedToSwap(address(pool), user, true);
// allowedSwapper[pool][user] = true
// allowedSwapper[pool][router] = false (default)

// User swaps through router
vm.prank(user);
router.exactInputSingle(ExactInputSingleParams({
    pool: address(pool),
    ...
}));
// pool.swap() is called with msg.sender = router
// _beforeSwap(router, ...) is called
// extension.beforeSwap(sender=router, ...) checks allowedSwapper[pool][router] => false
// => reverts NotAllowedToSwap, even though `user` is allowlisted
``` [4](#0-3) [5](#0-4)

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
