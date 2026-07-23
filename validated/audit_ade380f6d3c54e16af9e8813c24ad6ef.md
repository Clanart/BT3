Audit Report

## Title
Excess native ETH sent to `exactOutputSingle` / `exactOutput` is permanently stranded and stealable by any caller — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`, `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`exactOutputSingle` and `exactOutput` are `payable` but never refund surplus native ETH after the swap completes. `pay()` wraps only the exact amount the pool requests, leaving any excess `msg.value` sitting on the router. Because `refundETH()` is permissionless and sends the full contract ETH balance to `msg.sender`, any third party can call it in a subsequent transaction and steal the stranded ETH.

## Finding Description
`PeripheryPayments.pay()` handles native ETH by wrapping only the exact `value` the pool requests:

```solidity
// PeripheryPayments.sol L73-77
} else if (token == WETH) {
  uint256 nativeBalance = address(this).balance;
  if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // wraps exactly `value`, not nativeBalance
    IERC20(WETH).safeTransfer(recipient, value);
``` [1](#0-0) 

When `nativeBalance > value`, the difference remains as raw ETH on the router. Neither `exactOutputSingle` nor `exactOutput` calls `refundETH()` before returning:

```solidity
// MetricOmmSimpleRouter.sol L145-147
    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
    // ← no refundETH() here
``` [2](#0-1) [3](#0-2) 

`refundETH()` is permissionless and sends the entire contract balance to `msg.sender`, not to the original swap caller:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
  uint256 balance = address(this).balance;
  if (balance > 0) {
    _transferETH(msg.sender, balance);   // any caller receives all ETH
  }
}
``` [4](#0-3) 

The `receive()` fallback blocks unsolicited ETH from non-WETH addresses, but `payable` swap entry-points bypass it entirely, so the router accumulates ETH from any caller who sends excess `msg.value`. [5](#0-4) 

## Impact Explanation
A user calling `exactOutputSingle{value: X}` where `X > amountIn` (e.g., sending `amountInMaximum` as a slippage buffer in native ETH) loses `X − amountIn` ETH permanently if they do not atomically bundle a `refundETH()` call in the same `multicall`. This is a direct loss of user principal with no recovery path once the transaction is confirmed. The loss is bounded only by the user's slippage buffer, which can be arbitrarily large.

## Likelihood Explanation
The pattern is realistic: users interacting with `exactOutputSingle` for WETH-input swaps commonly send a native ETH buffer (e.g., `amountInMaximum` worth of ETH) to avoid a separate WETH approval. The interface NatSpec contains no warning that excess ETH will not be refunded automatically. Any MEV bot monitoring the mempool can steal the surplus in the same block with a trivial `refundETH()` call. The attack requires no special privileges and is repeatable against every such transaction. [6](#0-5) 

## Recommendation
Add an automatic ETH refund at the tail of every `payable` swap entry-point:

```solidity
function exactOutputSingle(…) external payable returns (uint256 amountIn) {
    …
    if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
    _clearExpectedCallbackPool();
    // Refund any unused native ETH to the caller
    uint256 leftover = address(this).balance;
    if (leftover > 0) _transferETH(msg.sender, leftover);
}
```

Apply the same fix to `exactOutput`. Alternatively, document clearly that callers **must** wrap these calls in `multicall([…, refundETH()])` when sending native ETH, and add an on-chain guard that reverts if `address(this).balance > 0` after the swap when not inside a multicall context.

## Proof of Concept
1. Alice wants to buy exactly 1,000 token1 using native ETH (WETH pool).
2. She estimates `amountIn ≈ 1.5 ETH` and sends `2 ETH` as a buffer:
   ```
   router.exactOutputSingle{value: 2 ether}(ExactOutputSingleParams{
       tokenIn: WETH, amountOut: 1_000, amountInMaximum: 2 ether, …
   });
   ```
3. Pool consumes 1.5 ETH worth of WETH; `pay()` wraps 1.5 ETH, leaving 0.5 ETH on the router.
4. `exactOutputSingle` returns; 0.5 ETH sits on the router with no refund.
5. Bob (front-runner) calls `router.refundETH()` in the next transaction.
6. `refundETH()` sends `address(this).balance` (0.5 ETH) to Bob.
7. Alice loses 0.5 ETH with no recourse.

Foundry test: deploy router, call `exactOutputSingle{value: 2 ether}` with a mock pool that consumes 1.5 ETH, assert `address(router).balance == 0.5 ether`, then call `refundETH()` from a different address and assert that address received 0.5 ETH. [7](#0-6) [6](#0-5)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L69-88)
```text
  function pay(address token, address payer, address recipient, uint256 value) internal {
    // If the payer is contract it means we are in the middle of a path. In the middle of a path we operate on ERC20 only.
    if (payer == address(this)) {
      IERC20(token).safeTransfer(recipient, value);
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
    } else {
      IERC20(token).safeTransferFrom(payer, recipient, value);
    }
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L186-188)
```text
    amountIn = _getExactOutputAmountIn();
    _clearExpectedCallbackPool();
  }
```
