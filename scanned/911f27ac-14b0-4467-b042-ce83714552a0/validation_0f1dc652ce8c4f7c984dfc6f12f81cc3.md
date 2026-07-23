Let me check the `IPeripheryPayments` interface and the `refundETH` function more carefully, and also look at how `multicall` interacts with this.

The vulnerability is real. Let me trace the exact execution path through the code.

**Execution trace for `exactInputSingle(tokenIn=WETH, amountIn=100, msg.value=200)`:**

1. `exactInputSingle` is `payable` — accepts 200 ETH with no validation that `msg.value == amountIn`. [1](#0-0) 

2. The pool callback fires `_justPayCallback` → `pay(token=WETH, payer=user, recipient=pool, value=100)`. [2](#0-1) 

3. Inside `pay`, the `token == WETH` branch reads `nativeBalance = address(this).balance` = 200. Since `200 >= 100`, it wraps exactly 100 ETH and transfers WETH to the pool. The remaining 100 ETH stays in the router with no refund. [3](#0-2) 

4. `refundETH()` sends **all** of the router's ETH balance to `msg.sender` — not just the caller's own ETH. Any subsequent caller can drain the stranded 100 ETH. [4](#0-3) 

5. Alternatively, a subsequent WETH swap with `msg.value=0` will consume the stranded ETH via the same `nativeBalance >= value` branch in `pay`, effectively getting a free swap funded by the victim. [5](#0-4) 

There is no guard in `exactInputSingle` enforcing `msg.value == amountIn` when `tokenIn == WETH`, and no automatic post-swap refund. The `receive()` guard (rejecting non-WETH ETH senders) does not apply to ETH sent via `msg.value` in a function call. [6](#0-5) 

---

### Title
Excess ETH stranded in router by WETH `exactInputSingle` is claimable by any subsequent caller — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`exactInputSingle` (and all other `payable` swap entry points) accept arbitrary `msg.value`. When `tokenIn == WETH` and `msg.value > amountIn`, the `pay` function wraps only `amountIn` worth of ETH and leaves the remainder in the router. Because `refundETH()` unconditionally sends the router's entire ETH balance to `msg.sender`, any subsequent caller can steal the stranded ETH. The same stranded ETH can also be silently consumed by a subsequent WETH swap via the `nativeBalance >= value` branch in `pay`.

### Finding Description
`PeripheryPayments.pay` uses `address(this).balance` as the available native ETH pool without any per-transaction accounting:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
```

This means ETH stranded from a prior transaction is indistinguishable from ETH sent in the current transaction. Combined with `refundETH()` sending the full router balance to any caller:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

there is no mechanism to return excess ETH to its rightful owner, and no mechanism to prevent a third party from claiming it.

### Impact Explanation
Direct, permanent loss of user ETH. The excess ETH (`msg.value - amountIn`) is immediately claimable by any address that calls `refundETH()` in a subsequent transaction, or is silently consumed to subsidize a subsequent WETH swap. No privileged access is required. The victim receives no output token shortfall — the swap itself succeeds — so the loss is invisible at the application layer unless the user monitors their ETH balance.

### Likelihood Explanation
Any user who calls `exactInputSingle` (or `exactOutput*`) with `tokenIn=WETH` and `msg.value > amountIn` without wrapping the call in a `multicall([swap, refundETH])` is vulnerable. Wallets and integrators that follow the Uniswap v3 pattern of sending `msg.value = amountInMaximum` for WETH exact-output swaps will trigger this on every such trade. The attack requires no setup: the attacker simply monitors the mempool for stranded ETH and calls `refundETH()`.

### Recommendation
Add a post-swap ETH balance check in each `payable` swap entry point and revert or auto-refund if `address(this).balance > 0` after the swap. Alternatively, enforce `msg.value == 0 || (tokenIn == WETH && msg.value == amountIn)` at entry, and auto-refund any remainder before returning. The safest fix is to track per-transaction ETH contributed via a transient variable and restrict `refundETH` to returning only that amount to `msg.sender`.

### Proof of Concept
```solidity
// Foundry test sketch
function test_excessEthStranded() public {
    // User swaps 100 WETH, sends 200 ETH
    uint256 amountOut = router.exactInputSingle{value: 200 ether}(
        IMetricOmmSimpleRouter.ExactInputSingleParams({
            pool: wethUsdcPool,
            tokenIn: WETH,
            tokenOut: USDC,
            zeroForOne: true,
            amountIn: 100 ether,
            amountOutMinimum: 0,
            recipient: user,
            deadline: block.timestamp + 1,
            priceLimitX64: 0,
            extensionData: ""
        })
    );
    // 100 ETH is now stranded in the router
    assertEq(address(router).balance, 100 ether);

    // Attacker claims it with no prior interaction
    vm.prank(attacker);
    router.refundETH();
    assertEq(attacker.balance, 100 ether);
    assertEq(address(router).balance, 0);
}
```

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L192-199)
```text
  function _justPayCallback(int256 amount0Delta, int256 amount1Delta) private {
    pay(
      _getTokenToPay(),
      _getPayer(),
      msg.sender,
      uint256(MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta))
    );
  }
```

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
