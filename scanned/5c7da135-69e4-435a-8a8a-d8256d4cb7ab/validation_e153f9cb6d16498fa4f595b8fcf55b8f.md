### Title
`exactOutputSingle` and `exactOutput` do not refund excess native ETH to the caller — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

### Summary

When a user calls `exactOutputSingle` (or `exactOutput`) with native ETH (`msg.value`) for a WETH-input swap, the `pay()` helper in `PeripheryPayments` consumes only the actual `amountIn` from the router's ETH balance. Any excess ETH (`msg.value − amountIn`) is silently stranded on the router. Because `refundETH()` is a public, attribution-free function that sends the **entire** router ETH balance to `msg.sender`, any third party can drain the stranded ETH in a subsequent transaction.

### Finding Description

**Exact call trace for `exactOutputSingle`:**

1. User calls `exactOutputSingle{value: 2 ETH}(params)` where `params.tokenIn = WETH` and `params.amountInMaximum = 2 ETH`.
2. The pool executes the swap and determines `amountIn = 1 ETH` (the actual cost).
3. The pool fires `metricOmmSwapCallback`, which calls `_justPayCallback`.
4. `_justPayCallback` calls `pay(WETH, originalCaller, pool, 1 ETH)`.
5. Inside `pay()`, the branch `token == WETH` is taken. `nativeBalance = address(this).balance = 2 ETH`. Since `nativeBalance >= value (1 ETH)`, only `1 ETH` is wrapped and transferred to the pool.
6. The remaining `1 ETH` stays on the router — `exactOutputSingle` returns without refunding it.
7. Any address can now call `refundETH()` and receive the stranded `1 ETH`. [1](#0-0) 

The `pay()` function correctly wraps only the required amount, but no code path in `exactOutputSingle` or `exactOutput` returns the remainder: [2](#0-1) 

`refundETH()` is public and sends the full router balance to `msg.sender` with no caller-identity check: [3](#0-2) 

The same stranding occurs in multi-hop `exactOutput` because the recursive callback path also calls `pay()` with only the settled `amountIn`: [4](#0-3) 

### Impact Explanation

A user who sends `msg.value > amountIn` (a natural and common pattern when setting `amountInMaximum` as a slippage cap) loses the difference permanently unless they happen to include `refundETH()` in the same `multicall`. If they call `exactOutputSingle` directly (not via `multicall`), the excess ETH is immediately claimable by any third party. This is a direct, unconditional loss of user principal with no recovery path once the transaction is mined.

### Likelihood Explanation

Exact-output swaps are specifically designed for users who want a guaranteed output amount and are willing to pay up to a maximum input. It is standard practice to set `amountInMaximum` conservatively above the expected cost. Any user who calls `exactOutputSingle` or `exactOutput` directly (without wrapping in a `multicall` that also calls `refundETH()`) will strand their excess ETH. The `receive()` guard blocks direct ETH deposits but does not prevent `msg.value` from accumulating via `payable` swap functions. [5](#0-4) 

### Recommendation

Add an automatic ETH refund at the end of `exactOutputSingle` and `exactOutput` (and their multi-hop equivalents) when the token-in is WETH and native ETH was used:

```solidity
// At the end of exactOutputSingle / exactOutput, after amountIn is known:
uint256 excess = address(this).balance;
if (excess > 0) {
    _transferETH(msg.sender, excess);
}
```

Alternatively, document prominently in the interface NatSpec that callers **must** include `refundETH()` in a `multicall` when sending native ETH, and add a test that asserts no ETH remains on the router after a standalone `exactOutputSingle` call with excess `msg.value`.

### Proof of Concept

```solidity
// Attacker setup: pool with WETH/token1, bid price ~1 ETH per 1500 token1 units
// User calls exactOutputSingle directly (not via multicall):
uint256 amountIn = router.exactOutputSingle{value: 2 ether}(
    IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: 1500,
        amountInMaximum: uint128(2 ether),  // slippage cap
        recipient: user,
        deadline: block.timestamp + 60,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// amountIn == ~1 ether (actual cost); 1 ether excess stays on router

// Attacker (any address) in a subsequent tx:
attacker.call(abi.encodeWithSelector(router.refundETH.selector));
// Attacker receives the user's 1 ether excess
assert(address(attacker).balance == 1 ether);
assert(address(router).balance == 0);
``` [1](#0-0) [3](#0-2)

### Citations

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L201-213)
```text
  function _exactOutputIterateCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) private {
    ExactOutputIterateCallbackData memory cb = abi.decode(data, (ExactOutputIterateCallbackData));

    int256 amountToPay = MetricOmmSwapResults.extractPositiveAmount(amount0Delta, amount1Delta);
    uint8 tradesLeft = _getTradesLeft();

    if (tradesLeft == 0) {
      // forge-lint: disable-next-line(unsafe-typecast)
      uint256 amountIn = uint256(amountToPay);
      if (amountIn > cb.amountInMax) revert InputTooHigh(amountIn, cb.amountInMax);
      _setExactOutputAmountIn(amountIn);
      pay(_getTokenToPay(), _getPayer(), msg.sender, amountIn);
      return;
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
