Audit Report

## Title
Cross-User ETH Drain via Unattributed Native Balance in `pay()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` reads `address(this).balance` — the router's global native ETH balance — when settling a WETH-input payment, with no per-user attribution. When a user calls `exactOutputSingle` with a native ETH buffer larger than the pool's actual input requirement, the surplus remains on the router. Any subsequent caller whose swap uses WETH as `tokenIn` will have their payment silently covered by the first user's stranded ETH, constituting a direct loss of user principal. Additionally, `refundETH()` is a public function that sends the entire router ETH balance to whoever calls it, providing an even simpler theft path for the same stranded ETH.

## Finding Description
`PeripheryPayments.pay()` handles WETH-input payments as follows:

```solidity
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
}
``` [1](#0-0) 

The function reads the router's **global** native balance and uses it to satisfy the current caller's WETH obligation. The `payer` identity stored in transient storage is only consulted in the `safeTransferFrom` branch (when native balance is zero); when native ETH is present, it is consumed without checking whether it belongs to the current caller. [2](#0-1) 

ETH accumulates on the router via any `payable` entry point. The canonical path is `exactOutputSingle`: the user sends `msg.value = amountInMaximum` as a buffer because the exact input is unknown before execution. The pool determines the real `amountIn`; the callback calls `pay()` which wraps only `amountIn` worth of ETH. The remainder (`msg.value − amountIn`) stays on the router. [3](#0-2) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does **not** prevent ETH from accumulating via `msg.value` in any `payable` function. [4](#0-3) 

`refundETH()` is a public function that sends the **entire** router ETH balance to `msg.sender` — any address can call it to directly steal stranded ETH: [5](#0-4) 

The same `pay()` path is reachable from `MetricOmmPoolLiquidityAdder.metricOmmModifyLiquidityCallback` when `token0` or `token1` is WETH, so the drain also applies to liquidity-add flows. [6](#0-5) 

There is no per-user ETH tracking anywhere in the transient callback context (`TransientCallbackPool`) — it stores payer identity and token-to-pay, but not how much native ETH the current caller is entitled to use. [7](#0-6) 

## Impact Explanation
Direct loss of user ETH principal. The victim's surplus ETH is consumed to pay a different user's swap obligation. The attacker pays zero WETH from their own wallet for the portion covered by the router's balance. The loss equals `min(victim_surplus, attacker_amountIn)` and can be up to the full `msg.value` the victim sent. No privileged role is required. Additionally, `refundETH()` allows any address to steal the entire stranded ETH balance in a single call, making the full `msg.value − amountIn` immediately extractable. This meets the Critical/High direct loss of user principal threshold under the contest rules.

## Likelihood Explanation
`exactOutputSingle` is the canonical use-case for sending a native ETH buffer because the exact input is unknowable before execution. The Uniswap v3 multicall pattern (swap + `refundETH`) is well-documented, but users calling `exactOutputSingle` directly (not via `multicall`) have no in-transaction refund path. MEV searchers can observe the victim's transaction in the mempool and insert either a `refundETH()` call or a WETH-input swap immediately after it. The `MetricOmmSimpleRouter` exposes `exactOutputSingle` as a standalone `payable` function, making the single-call (no-refund) pattern easy to reach. [8](#0-7) 

## Recommendation
Track the ETH that belongs to the current caller rather than reading the global balance. One approach: record `msg.value` in transient storage at the start of each entry point and consume only that amount in `pay()`, reverting if the caller's credited balance is insufficient. Alternatively, restrict `exactOutputSingle` (and all other payable swap entry points) to only be callable via `multicall`, which can include a mandatory `refundETH()` call, so the surplus is always returned in the same atomic transaction. Additionally, `refundETH()` should be restricted to only be callable within a `multicall` context, or should require the caller to be the original `msg.sender` of the outer transaction.

## Proof of Concept
```solidity
// 1. Victim calls exactOutputSingle with 1 ETH buffer; pool only needs 0.5 ETH.
//    Surplus 0.5 ETH stays on the router.
router.exactOutputSingle{value: 1 ether}(ExactOutputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountOut: 1_000,
    amountInMaximum: 1 ether,
    recipient: victim,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// router.balance == 0.5 ether (victim's unrefunded surplus)

// 2a. Direct theft: attacker calls refundETH() to steal the entire surplus.
router.refundETH(); // attacker receives 0.5 ETH

// OR

// 2b. Attacker calls exactInputSingle with WETH as tokenIn, zero msg.value.
//     pay() sees nativeBalance = 0.5 ETH, wraps it, and forwards it to the pool.
//     Attacker's WETH allowance is NOT touched; victim's ETH pays the swap.
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 300,          // 300 wei < 0.5 ETH surplus
    amountOutMinimum: 0,
    recipient: attacker,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// Attacker receives token1 output; victim's ETH balance on router reduced by 300 wei.
assertEq(address(router).balance, 0.5 ether - 300); // victim's ETH drained
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

**File:** metric-periphery/contracts/MetricOmmPoolLiquidityAdder.sol (L172-177)
```text
    if (amount0Delta > 0) {
      pay(token0, payer, msg.sender, amount0Delta);
    }
    if (amount1Delta > 0) {
      pay(token1, payer, msg.sender, amount1Delta);
    }
```

**File:** metric-periphery/contracts/libraries/TransientCallbackPool.sol (L10-13)
```text
  uint256 private constant T_SLOT = 0;
  uint256 private constant T_AMOUNT_IN_SLOT = 1;
  uint256 private constant T_PAYER_SLOT = 2;
  uint256 private constant T_TOKEN_TO_PAY_SLOT = 3;
```
