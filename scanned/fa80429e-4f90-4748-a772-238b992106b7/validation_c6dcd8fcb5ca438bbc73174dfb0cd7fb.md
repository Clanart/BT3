The vulnerability is real and confirmed by the code. Here is the full finding:

---

### Title
Unprivileged `refundETH()` allows any caller to steal stranded ETH from users who overpay in payable swap functions — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`refundETH()` is an unrestricted public function that sends the router's entire native ETH balance to `msg.sender`. The `pay()` helper, when `tokenIn == WETH`, deposits exactly `amountIn` worth of native ETH and leaves any excess on the router. A user who calls `exactInputSingle` (or any other payable swap entry point) with `msg.value > amountIn` and does not atomically append a `refundETH()` step via `multicall` will have their excess ETH permanently stranded on the router, where any attacker can claim it in a subsequent transaction.

---

### Finding Description

`refundETH()` has no caller restriction: [1](#0-0) 

It unconditionally transfers `address(this).balance` to `msg.sender`. There is no check that `msg.sender` is the address that originally deposited the ETH.

The `pay()` function, when `token == WETH` and `nativeBalance >= value`, deposits exactly `value` ETH as WETH and transfers it to the pool, leaving `nativeBalance - value` ETH on the router with no refund: [2](#0-1) 

`exactInputSingle` is `payable` and performs no automatic refund after the swap completes: [3](#0-2) 

The same applies to `exactInput`, `exactOutputSingle`, and `exactOutput`, all of which are `payable` and do not refund excess ETH. [4](#0-3) 

The `receive()` guard only prevents direct ETH transfers from non-WETH addresses; it does not prevent ETH from being sent alongside a `payable` function call: [5](#0-4) 

The intended safe pattern is to use `multicall` with `refundETH()` as the final step, as shown in the test suite: [6](#0-5) 

However, nothing in the contract enforces this pattern. A user who calls `exactInputSingle` directly (not via `multicall`) with excess ETH will strand the difference on the router, where it is immediately claimable by any address.

---

### Impact Explanation

Direct loss of user principal. Any ETH sent in excess of `amountIn` during a WETH-input swap is permanently stranded on the router after the transaction and can be claimed by any unprivileged caller via `refundETH()`. The attacker receives the victim's ETH with no preconditions beyond observing the stranded balance.

---

### Likelihood Explanation

Moderate-to-high. Users interacting with the router directly (not through a frontend that constructs the correct `multicall`) will naturally send a round ETH value and expect change back, exactly as they would with a DEX aggregator. Mempool monitoring for `refundETH()` opportunities is trivial. The attack requires no special privileges, no malicious pool, and no non-standard token behavior.

---

### Recommendation

Add an automatic ETH refund at the end of each payable swap entry point, or restrict `refundETH()` so it can only be called within a `multicall` context (e.g., via a transient reentrancy flag set by `multicall`). The simplest fix is to call `_refundETH(msg.sender)` at the end of `exactInputSingle`, `exactInput`, `exactOutputSingle`, and `exactOutput` when `address(this).balance > 0`. Alternatively, document and enforce that these functions must only be called through `multicall` with an explicit `refundETH()` step, and add a guard that reverts direct (non-multicall) calls when `msg.value > 0`.

---

### Proof of Concept

```
// 1. User calls exactInputSingle directly (not via multicall) with excess ETH
router.exactInputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 0.5 ether,      // only 0.5 ETH is consumed by pay()
        amountOutMinimum: 0,
        recipient: user,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// After this tx: router holds 0.5 ETH, user is missing 0.5 ETH

// 2. Attacker calls refundETH() in a separate transaction
vm.prank(attacker);
router.refundETH();

// Assert: attacker received 0.5 ETH, user's ETH is gone
assertEq(attacker.balance, 0.5 ether);
assertEq(address(router).balance, 0);
```

The `pay()` function deposits exactly `amountIn = 0.5 ether` as WETH (line 76–77 of `PeripheryPayments.sol`), leaving the remaining `0.5 ether` in `address(this).balance`. `refundETH()` then transfers the full balance to `msg.sender` with no origin check (line 61 of `PeripheryPayments.sol`). [1](#0-0) [2](#0-1)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-125)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint256 last = params.pools.length - 1;
    int128 amount = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountIn);

    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

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

      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);

      amount = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    }

    if (amount <= 0) revert InvalidSwapDeltas();
    amountOut = MetricOmmSwapInputs.int128ToUint128(amount);
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
