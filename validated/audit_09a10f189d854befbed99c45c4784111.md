Audit Report

## Title
Router ETH Balance Consumed by Subsequent WETH Swaps, Stealing Prior User's Funds — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

The `pay` function in `PeripheryPayments` checks `address(this).balance` to decide whether to wrap native ETH for a WETH payment, even when `payer` is an external user. If a prior user's `exactOutputSingle` call leaves residual ETH in the router (by omitting `refundETH()`), any subsequent WETH-input swap whose amount is ≤ that residual balance will be silently funded from the victim's ETH, with the attacker's WETH allowance never touched and the victim's ETH permanently lost.

## Finding Description

`PeripheryPayments.pay` at L73–84 contains a WETH branch that, when `payer != address(this)`, still checks `address(this).balance` first: [1](#0-0) 

When `nativeBalance >= value`, the router wraps its own ETH and transfers WETH to the recipient — the `payer` argument is completely ignored. No `safeTransferFrom(payer, ...)` is ever called.

ETH enters the router legitimately via `msg.value` on `exactOutputSingle` (which is `payable`): [2](#0-1) 

The `receive()` guard only blocks plain ETH transfers (not `msg.sender == WETH`), not `msg.value` attached to a function call: [3](#0-2) 

`exactOutputSingle` has no automatic `refundETH()` at the end, so if `msg.value > actualAmountIn`, the excess ETH is stranded in the router. The callback path that triggers `pay` with the victim's address as `payer`: [4](#0-3) [5](#0-4) 

When the attacker's subsequent swap triggers `_justPayCallback`, `_getPayer()` returns the attacker's address, but the `nativeBalance >= value` branch never calls `safeTransferFrom(attacker, ...)` — it consumes the victim's stranded ETH instead.

## Impact Explanation

Direct loss of user principal (ETH). The victim loses up to `amountInMaximum − actualAmountIn` ETH per transaction. The attacker receives a fully subsidised swap: pool receives correct WETH (wrapped from victim's ETH), attacker's WETH allowance is untouched, and the victim's ETH is permanently gone. This is a Critical/High direct fund loss matching the allowed impact gate.

## Likelihood Explanation

`exactOutput` swaps with ETH/WETH are a standard user flow. Omitting `refundETH()` from a multicall is a well-documented footgun in Uniswap-style routers. The router's ETH balance is publicly readable on-chain. An attacker requires zero special privileges: they simply monitor `address(router).balance`, then call `exactInputSingle` with `tokenIn = WETH` and `amountIn ≤ residual`. The attack is repeatable and can be front-run or immediately follow any stranding transaction.

## Recommendation

Remove the dependency on `address(this).balance` when `payer` is an external address. The ETH-wrapping shortcut should only apply when `payer == address(this)`:

```solidity
} else if (token == WETH) {
    if (payer == address(this)) {
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
    } else {
        // External payer: always pull WETH; never consume router's ETH on their behalf
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
}
```

Alternatively, add a post-call invariant check that reverts if `address(this).balance > 0` at the end of any top-level entry point, or enforce that every `exactOutput` multicall with `msg.value > 0` must include `refundETH()` as the final call.

## Proof of Concept

1. **Victim** calls `exactOutputSingle({tokenIn: WETH, amountOut: X, amountInMaximum: 1 ether, ...})` with `msg.value = 1 ether`. Actual pool input required is `0.9 ether`. Callback fires `pay(WETH, victim, pool, 0.9 ether)` → `nativeBalance = 1 ether ≥ 0.9 ether` → router wraps `0.9 ether`, sends WETH to pool. `0.1 ether` remains in router. Victim omits `refundETH()`.

2. **Attacker** calls `exactInputSingle({tokenIn: WETH, amountIn: 0.1 ether, ...})` with `msg.value = 0`. Callback fires `pay(WETH, attacker, pool, 0.1 ether)` → `nativeBalance = 0.1 ether ≥ 0.1 ether` → router wraps victim's `0.1 ether`, sends WETH to pool. Attacker's WETH allowance is never touched.

3. Attacker receives swap output. Victim's `0.1 ether` is permanently lost.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L71-71)
```text
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
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
