Audit Report

## Title
Excess native ETH sent to payable swap functions is permanently stranded and can be stolen via `refundETH()` - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
All four swap entry-points on `MetricOmmSimpleRouter` are `payable`, but `pay()` wraps only the exact `value` the pool requests, leaving any surplus ETH in the router. Because `refundETH()` is an unrestricted public function that forwards the entire contract ETH balance to `msg.sender`, any third party can claim that surplus before the original caller reclaims it.

## Finding Description
Every swap entry-point is `payable`: [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

When `tokenIn == WETH`, the swap callback triggers `_justPayCallback` → `pay()`. Inside `pay()`, the native ETH branch uses `>=`:

```solidity
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // wraps only `value`
    IERC20(WETH).safeTransfer(recipient, value);
}
``` [5](#0-4) 

Any `nativeBalance - value` surplus remains in the router after the swap completes. None of the four swap functions issue an automatic refund after settlement. [6](#0-5) 

`refundETH()` has no access control and sends the full contract ETH balance to whoever calls it:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [7](#0-6) 

The `receive()` guard only blocks plain ETH transfers from non-WETH addresses; it does not block ETH attached to a function call (e.g., `refundETH{value: 0}()`), so the guard provides no protection here: [8](#0-7) 

## Impact Explanation
A user who calls `exactInputSingle{value: X}(params)` with `tokenIn == WETH` and `X > amountIn` loses `X - amountIn` ETH permanently if a front-runner calls `refundETH()` first. This is a direct loss of user principal with no recovery path. The impact is **High**: user ETH is stolen, not merely locked.

## Likelihood Explanation
**Likelihood: Low.** The scenario requires the user to attach more ETH than the swap consumes. This can occur via a buggy integration, a slippage miscalculation, or a user manually over-funding a WETH-input swap. The multicall pattern (`exactInputSingle` + `refundETH` atomically) is the intended safe path, but a direct single call with excess ETH is a realistic mistake and the contract provides no protection against it.

## Recommendation
After the swap settles, refund any remaining native balance to `msg.sender` inside each `payable` swap function:

```solidity
// At the end of exactInputSingle / exactInput / exactOutputSingle / exactOutput:
if (address(this).balance > 0) {
    _transferETH(msg.sender, address(this).balance);
}
```

Alternatively, tighten `pay()` to reject surplus ETH:

```diff
- if (nativeBalance >= value) {
+ if (nativeBalance == value) {
```

## Proof of Concept
1. Pool has a WETH/USDC pair. `amountIn` for a given swap is `1 ETH`.
2. User calls `exactInputSingle{value: 2 ether}(params)` with `tokenIn = WETH`.
3. Pool triggers `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, user, pool, 1e18)`.
4. `pay()` sees `nativeBalance = 2e18 >= 1e18 = value`, wraps `1e18`, transfers to pool. `1e18` ETH remains in router.
5. Attacker observes the transaction (in mempool or after inclusion) and calls `refundETH()`.
6. `refundETH()` sends the full `1e18` ETH balance to the attacker.
7. User receives correct swap output but loses `1 ETH` of excess input with no recourse.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L92-92)
```text
  function exactInput(ExactInputParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L130-130)
```text
  function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-154)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L75-77)
```text
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```
