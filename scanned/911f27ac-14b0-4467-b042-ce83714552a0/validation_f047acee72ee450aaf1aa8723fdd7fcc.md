### Title
Excess ETH Sent to `MetricOmmSimpleRouter` Is Not Refunded and Is Stealable by Any Caller via `refundETH()` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`, `metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

When a user calls any `payable` swap function on `MetricOmmSimpleRouter` with `tokenIn == WETH` and sends more native ETH than the pool actually consumes, the surplus ETH is silently retained by the router. The only recovery path is `refundETH()`, but that function sends the entire ETH balance to `msg.sender` — not to the original depositor — so any third party (MEV bot, front-runner) can steal the stranded ETH before the user reclaims it.

---

### Finding Description

`PeripheryPayments.pay()` handles the ETH→WETH conversion path as follows: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();          // wraps exactly `value`
        IERC20(WETH).safeTransfer(recipient, value);
    } else if (nativeBalance > 0) {
        IWETH9(WETH).deposit{value: nativeBalance}();
        IERC20(WETH).safeTransfer(recipient, nativeBalance);
        IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
    } else {
        IERC20(WETH).safeTransferFrom(payer, recipient, value);
    }
```

When `nativeBalance >= value`, the function wraps and forwards exactly `value` wei. Any ETH above `value` that was sent with the transaction is **not consumed and not returned** — it remains in the router.

This is directly reachable through `exactOutputSingle`: [2](#0-1) 

The user does not know the exact `amountIn` before the transaction executes (the pool determines it). The natural pattern is to send `msg.value == params.amountInMaximum` in ETH to guarantee the swap succeeds. The pool then charges `amountIn ≤ amountInMaximum`; the callback calls `pay(WETH, msg.sender, pool, amountIn)`, which wraps only `amountIn`. The delta `msg.value − amountIn` is left in the router with no automatic refund.

The recovery function is: [3](#0-2) 

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends to caller, not original depositor
    }
}
```

`refundETH()` is permissionless and sends the **entire** ETH balance to whoever calls it. A front-runner or MEV bot that observes the swap transaction in the mempool (or in the same block) can call `refundETH()` immediately after and receive the victim's surplus ETH.

The same exposure exists in `exactOutput` (multi-hop) and `exactInputSingle`/`exactInput` when the user over-sends ETH relative to `params.amountIn`. [4](#0-3) 

---

### Impact Explanation

Direct loss of user ETH principal. The stranded amount equals `msg.value − actual amountIn`, which for exact-output swaps can be a meaningful fraction of the swap size (up to `amountInMaximum − amountIn`). The theft requires no special privilege — any EOA or bot can call `refundETH()` in the same block.

---

### Likelihood Explanation

High. Exact-output ETH swaps are a standard UX pattern. Users and front-ends routinely send `amountInMaximum` in ETH to cover worst-case slippage. MEV infrastructure already monitors routers for stranded ETH. The attack requires a single permissionless call with no setup cost.

---

### Recommendation

Two complementary fixes:

1. **Auto-refund in the swap functions**: After each `payable` swap completes, refund any remaining `address(this).balance` to `msg.sender` before returning. This mirrors Uniswap v3's `refundETH` being called inside `multicall` but makes it mandatory rather than optional.

2. **Restrict `refundETH` to the original depositor**: Change `refundETH` to accept a `recipient` parameter supplied by the caller, or record the depositor in transient storage at swap entry and enforce it inside `refundETH`. At minimum, document that `refundETH` must always be batched in the same `multicall` as the swap.

---

### Proof of Concept

1. Alice calls `exactOutputSingle` with `tokenIn = WETH`, `amountOut = 1000 USDC`, `amountInMaximum = 1 ETH`, and sends `msg.value = 1 ETH`.
2. The pool charges `0.8 ETH` as `amountIn`; the callback calls `pay(WETH, Alice, pool, 0.8 ETH)`.
3. `pay` wraps `0.8 ETH` and transfers it to the pool. `0.2 ETH` remains in the router.
4. `exactOutputSingle` returns; Alice's transaction is complete.
5. Bob (MEV bot) calls `refundETH()` in the same block. `_transferETH(Bob, 0.2 ETH)` executes.
6. Alice loses `0.2 ETH`; Bob gains it with zero cost. [5](#0-4) [2](#0-1)

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
