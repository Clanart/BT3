### Title
Excess native ETH sent to `exactOutputSingle`/`exactOutput` is silently stranded in the router and stealable by any caller via `refundETH()` — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

`exactOutputSingle` and `exactOutput` are `payable` and support native-ETH input (WETH as `tokenIn`). The internal `pay()` helper wraps only the exact `amountIn` the pool requested, leaving any excess `msg.value` sitting in the router. Neither function refunds the remainder. Because `refundETH()` sends the entire router ETH balance to `msg.sender` (whoever calls it, not the original swapper), any third party can immediately drain the stranded ETH.

---

### Finding Description

`PeripheryPayments.pay()` handles native ETH by checking `nativeBalance >= value` and wrapping exactly `value`: [1](#0-0) 

When `msg.value > actualAmountIn`, the difference `msg.value - actualAmountIn` is never consumed. It remains as raw ETH on the router after the swap call returns.

`exactOutputSingle` ends at line 146 with `_clearExpectedCallbackPool()` — no refund: [2](#0-1) 

`exactOutput` ends at line 187 with `_clearExpectedCallbackPool()` — no refund: [3](#0-2) 

`refundETH()` is permissionless and sends the full router ETH balance to `msg.sender`: [4](#0-3) 

Any address — not the original swapper — can call `refundETH()` in a follow-up transaction and receive the stranded ETH.

---

### Impact Explanation

Direct loss of user principal. A user who calls `exactOutputSingle{value: amountInMaximum}` loses `amountInMaximum - actualAmountIn` ETH to the first bot or MEV searcher that calls `refundETH()` in the same or next block. The loss is proportional to the gap between the user's slippage budget and the actual fill price; on volatile assets this can be substantial.

---

### Likelihood Explanation

For exact-output swaps the caller cannot know `actualAmountIn` before execution, so sending `msg.value = amountInMaximum` is the natural pattern. The documented multicall pattern (`exactOutputSingle` + `refundETH`) is the safe path, but:

- The functions are individually `payable` and callable without multicall.
- No NatSpec on `exactOutputSingle` or `exactOutput` warns that excess ETH is not refunded.
- Integrators or EOA users calling the function directly (not via multicall) will silently lose the excess.
- MEV bots routinely monitor for stranded ETH on router contracts.

---

### Recommendation

Add an automatic refund at the end of both exact-output entry points when `tokenIn == WETH`:

```solidity
// after _clearExpectedCallbackPool()
uint256 leftover = address(this).balance;
if (leftover > 0) _transferETH(msg.sender, leftover);
```

Alternatively, document with a prominent NatSpec warning that callers **must** append `refundETH()` in a multicall when paying with native ETH, and enforce this at the interface level by rejecting `msg.value > 0` on non-multicall paths.

---

### Proof of Concept

```solidity
// Attacker watches the mempool for exactOutputSingle calls with excess msg.value.
// After the victim's tx lands:

contract Thief {
    function steal(address router) external {
        // refundETH() sends router.balance to msg.sender (this contract)
        IPeripheryPayments(router).refundETH();
        payable(msg.sender).transfer(address(this).balance);
    }
    receive() external payable {}
}
```

Step-by-step:

1. Victim calls `router.exactOutputSingle{value: 1 ether}(params)` where `amountInMaximum = 1 ether` and the pool fills at `actualAmountIn = 0.6 ether`.
2. `pay()` wraps 0.6 ETH → WETH and transfers to pool. 0.4 ETH remains in router. [5](#0-4) 
3. `exactOutputSingle` returns. Router holds 0.4 ETH with no owner record.
4. Attacker calls `router.refundETH()`. Router sends 0.4 ETH to attacker. [6](#0-5) 
5. Victim loses 0.4 ETH.

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L58-63)
```text
  function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
      _transferETH(msg.sender, balance);
    }
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L154-188)
```text
  function exactOutput(ExactOutputParams calldata params) external payable returns (uint256 amountIn) {
    _checkDeadline(params.deadline);
    _validatePath(params.tokens, params.pools, params.extensionDatas);

    uint8 tradesLeftAfterThis = uint8(params.pools.length - 1);
    address pool = params.pools[tradesLeftAfterThis];
    bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, tradesLeftAfterThis);
    int128 expectedAmountOut = MetricOmmSwapInputs.asAmountSpecifiedIn(params.amountOut);
    _initCallbackContextforRecursiveOutput(
      pool, CALLBACK_MODE_EXACT_OUTPUT_ITERATE, tradesLeftAfterThis, msg.sender, params.tokens[0]
    );
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(pool)
      .swap(
        params.recipient,
        zeroForOne,
        -expectedAmountOut,
        MetricOmmSwapPath.openLimit(zeroForOne),
        abi.encode(
          ExactOutputIterateCallbackData({
          tokens: params.tokens,
          pools: params.pools,
          extensionDatas: params.extensionDatas,
          zeroForOneBitMap: params.zeroForOneBitMap,
          amountInMax: params.amountInMaximum
        })
        ),
        params.extensionDatas[tradesLeftAfterThis]
      );

    int128 amountOut = MetricOmmSwapResults.extractAmountOut(zeroForOne, amount0Delta, amount1Delta);
    if (amountOut != expectedAmountOut) revert InvalidOutputAmount(amountOut, params.amountOut);

    amountIn = _getExactOutputAmountIn();
    _clearExpectedCallbackPool();
  }
```
