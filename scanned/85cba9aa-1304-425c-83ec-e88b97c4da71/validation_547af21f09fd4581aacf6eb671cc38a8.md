The vulnerability is real. Let me confirm the exact mechanics before writing the finding.

The `refundETH()` function has no access control and sends the entire ETH balance to `msg.sender`: [1](#0-0) 

The `pay()` function, when `tokenIn == WETH` and `nativeBalance >= value`, wraps only `value` ETH — leaving the surplus native ETH in the router: [2](#0-1) 

`exactInputSingle` is `payable`, so ETH enters the router's balance on the call. The `receive()` guard only blocks plain ETH transfers, not payable function calls: [3](#0-2) 

The intended safe pattern (shown in tests) is `multicall{value}([exactInputSingle(...), refundETH()])` — but `exactInputSingle` is directly callable as `payable` with no enforcement of the multicall pattern: [4](#0-3) 

---

### Title
Unguarded `refundETH()` allows any caller to steal leftover ETH from users who overpay `exactInputSingle` without an atomic refund step — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
`PeripheryPayments.refundETH()` transfers the router's entire native ETH balance to `msg.sender` with no access control. When a user calls `exactInputSingle{value: V}` with `tokenIn = WETH` and `amountIn < V`, the `pay()` helper wraps only `amountIn` ETH and leaves `V - amountIn` stranded in the router. Any attacker who calls `refundETH()` in a subsequent transaction receives that surplus ETH.

### Finding Description
`PeripheryPayments.refundETH()` is `external payable` with no caller restriction:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);  // sends to whoever calls
    }
}
```

When `exactInputSingle` is called with `tokenIn = WETH` and `msg.value > amountIn`, the `pay()` function wraps exactly `amountIn` ETH:

```solidity
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // wraps only `value`, not all ETH
    IERC20(WETH).safeTransfer(recipient, value);
}
```

The remaining `msg.value - amountIn` ETH stays in the router after the swap returns. Because `exactInputSingle` is directly `payable` (not restricted to `multicall`), a user can legitimately call it standalone with excess ETH. The `receive()` guard only blocks plain ETH transfers — it does not prevent ETH from entering via payable function calls.

An attacker monitoring the chain can call `refundETH()` in the next block and drain the stranded ETH.

### Impact Explanation
Direct theft of user principal. The victim loses `msg.value - amountIn` ETH with no recourse. Impact is **High**: the attacker receives real ETH that belonged to the user, with no protocol mechanism to recover it.

### Likelihood Explanation
**Medium-High.** The attack requires no special privileges — just a call to a public function. The surplus ETH window exists between the user's swap transaction and the next block. MEV bots routinely monitor for exactly this pattern. Users who call `exactInputSingle` directly (rather than via `multicall`) are at risk whenever `msg.value > amountIn`.

### Recommendation
Either:
1. **Restrict `refundETH` to a stored recipient** — record `msg.sender` at the start of each top-level payable entry point (or in `multicall`) and only allow refund to that address; or
2. **Auto-refund surplus ETH** at the end of `exactInputSingle` / `exactOutput*` — after the swap, if `address(this).balance > 0`, transfer it back to `msg.sender` unconditionally; or
3. **Remove the standalone `payable` on swap functions** and require all ETH-input flows to go through `multicall`, enforcing that `refundETH` is always the last call in the batch.

### Proof of Concept
```solidity
// 1. User calls exactInputSingle directly with excess ETH
router.exactInputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 0.5 ether,   // only 0.5 ETH is wrapped and sent to pool
        amountOutMinimum: 0,
        recipient: user,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// router now holds 0.5 ETH (the surplus)

// 2. Attacker calls refundETH() in a separate tx
vm.prank(attacker);
router.refundETH();

// 3. Attacker receives 0.5 ETH; user's surplus is gone
assertEq(attacker.balance, 0.5 ether);
assertEq(address(router).balance, 0);
```

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
