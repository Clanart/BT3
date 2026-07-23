Audit Report

## Title
Excess ETH sent with payable swap functions is permanently stranded and claimable by any caller via `refundETH()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
When `exactInputSingle` (or `exactInput`, `exactOutputSingle`, `exactOutput`) is called with `tokenIn == WETH` and `msg.value > amountIn`, the `pay()` function wraps only the required `amountIn` worth of ETH and sends WETH to the pool. The surplus ETH remains in the router with no automatic refund. The permissionless `refundETH()` function sends the entire router ETH balance to any caller, allowing an attacker to steal the stranded surplus.

## Finding Description
`exactInputSingle` is `payable` and performs no post-swap ETH refund. [1](#0-0) 

Inside the callback, `pay()` is called with the exact swap delta as `value`, not `msg.value`. [2](#0-1) 

In `pay()`, when `token == WETH` and `nativeBalance >= value`, only `value` is wrapped and transferred; the remainder stays in the router. [3](#0-2) 

`refundETH()` is permissionless: it sends `address(this).balance` to `msg.sender` with no ownership check. [4](#0-3) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks direct ETH transfers with no calldata; it does not protect against an attacker calling the `payable` `refundETH()` function. [5](#0-4) 

The same stranding applies to `exactInput` (first hop, `i==0`, payer=`msg.sender`), `exactOutputSingle`, and `exactOutput` — all are `payable` with no post-swap ETH refund. [6](#0-5) 

## Impact Explanation
Direct loss of user ETH principal. A user sending `msg.value = 1e18` with `amountIn = 0.5e18` loses 0.5e18 ETH to any caller of `refundETH()` in a subsequent transaction. Loss scales linearly with surplus. This meets the Sherlock threshold for a High-severity direct loss of user principal.

## Likelihood Explanation
Medium-to-High. Any user who sends excess ETH (e.g., to account for slippage on exact-input WETH swaps, or who miscalculates `amountIn`) is exposed. MEV bots already monitor routers for stranded ETH and can extract it in the same block. Frontends that do not bundle `refundETH()` in a `multicall` leave users permanently exposed; this bundling is not enforced or documented anywhere in the router.

## Recommendation
Add an automatic ETH refund at the end of each payable swap function:

```solidity
// at the end of exactInputSingle, exactInput, exactOutputSingle, exactOutput
uint256 surplus = address(this).balance;
if (surplus > 0) _transferETH(msg.sender, surplus);
```

Alternatively, when `tokenIn == WETH`, enforce `msg.value == amountIn` and revert otherwise, eliminating the ambiguity entirely.

## Proof of Concept
1. User calls `exactInputSingle({tokenIn: WETH, amountIn: 0.5e18, ...})` with `msg.value = 1e18`.
2. `_justPayCallback` → `pay(WETH, msg.sender, pool, 0.5e18)`: `nativeBalance (1e18) >= value (0.5e18)`, so wraps 0.5e18 ETH → sends WETH to pool. Router now holds 0.5e18 ETH.
3. `exactInputSingle` returns after `_clearExpectedCallbackPool()`. No refund issued.
4. Attacker calls `refundETH()`. Router sends 0.5e18 ETH to attacker.
5. User's 0.5e18 ETH is permanently lost.

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
