### Title
Stranded ETH on Router Consumed by Subsequent User's WETH Payment — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay` uses `address(this).balance` as a global, unaccounted ETH pool when routing WETH payments. Any ETH left on the router from a prior user's transaction (e.g., excess `msg.value` not refunded) is silently consumed to subsidize a subsequent user's WETH swap, causing direct loss of the first user's ETH.

---

### Finding Description

The partial-ETH branch of `pay` reads the router's entire native balance without any per-user accounting: [1](#0-0) 

```solidity
uint256 nativeBalance = address(this).balance;   // global, not per-user
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
} else if (nativeBalance > 0) {
    IWETH9(WETH).deposit{value: nativeBalance}();
    IERC20(WETH).safeTransfer(recipient, nativeBalance);
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
}
```

The protocol's documented ETH pattern requires users to append `refundETH()` to their multicall to recover unused ETH: [2](#0-1) 

If a user omits `refundETH()` (or sends excess `msg.value` in a non-multicall path), the ETH remains on the router between transactions. The next user who calls any WETH `exactInput*` function will have their `pay` call hit the `nativeBalance > 0 && nativeBalance < value` branch, consuming the stranded ETH and reducing the `transferFrom` pull from the legitimate payer by exactly `nativeBalance`.

**Concrete attack path:**

1. User A calls `exactInputSingle{value: 2 ETH}(amountIn=1 ETH, tokenIn=WETH, ...)` without `refundETH()`. The swap uses 1 ETH; 1 ETH remains on the router.
2. User B calls `exactInputSingle(amountIn=1.5 ETH, tokenIn=WETH, ...)` with no ETH sent, relying on WETH allowance.
3. In the swap callback, `pay(WETH, userB, pool, 1.5 ETH)` is invoked. `nativeBalance = 1 ETH > 0` and `< 1.5 ETH`.
4. Router wraps and forwards user A's 1 ETH as WETH to the pool.
5. Router pulls only `0.5 ETH` worth of WETH from user B via `transferFrom`.
6. User B receives a full 1.5 ETH swap while paying only 0.5 ETH from their allowance. User A loses 1 ETH permanently.

The `payer` stored in transient context is `msg.sender` of the current call (user B), but the ETH consumed belongs to user A. [3](#0-2) 

---

### Impact Explanation

Direct loss of user principal: user A's ETH is irreversibly transferred to a pool to settle user B's swap. User B receives a subsidized trade. The router holds no record of whose ETH it is, so there is no recovery path. This meets the High threshold (unauthorized consumption of another user's ETH, direct fund loss).

---

### Likelihood Explanation

- The protocol's own tests show the pattern of sending excess ETH and relying on `refundETH()` to clean up.
- Users who call `exactInputSingle` directly (not via multicall) with `msg.value > amountIn` will always leave residual ETH.
- A griever or MEV bot can monitor the router's ETH balance and immediately follow with a WETH swap to capture the subsidy.
- No special permissions or malicious pool setup required — only two sequential public router calls.

---

### Recommendation

Track per-call ETH entitlement rather than using the global balance. One approach: record `msg.value` at multicall entry in transient storage and deduct from it as ETH is consumed in `pay`, reverting if the per-call budget is exceeded. Alternatively, restrict the ETH-to-WETH conversion in `pay` to only the ETH that arrived in the current top-level call by passing the available ETH budget explicitly.

---

### Proof of Concept

```solidity
// Foundry test sketch
function test_strandedEthSubsidizesOtherUser() public {
    // User A sends 2 ETH but swaps only 1 ETH worth of WETH, no refundETH
    vm.prank(userA);
    router.exactInputSingle{value: 2 ether}(
        ExactInputSingleParams({tokenIn: WETH, amountIn: 1 ether, ...})
    );
    // Router now holds 1 ETH (user A's unrefunded ETH)
    assertEq(address(router).balance, 1 ether);

    uint256 userBWethBefore = weth.balanceOf(userB);
    // User B swaps 1.5 ETH worth of WETH, sends no ETH
    vm.prank(userB);
    router.exactInputSingle(
        ExactInputSingleParams({tokenIn: WETH, amountIn: 1.5 ether, ...})
    );

    // User B's WETH allowance was only reduced by 0.5 ETH (not 1.5 ETH)
    assertEq(userBWethBefore - weth.balanceOf(userB), 0.5 ether);
    // User A's 1 ETH is gone, router is empty
    assertEq(address(router).balance, 0);
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-84)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
      } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
      }
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
```
