### Title
Excess ETH sent to `exactOutputSingle` / `exactOutput` is not refunded and is immediately stealable by any caller via `refundETH()` — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

`exactOutputSingle` and `exactOutput` are `payable` and accept native ETH as a WETH substitute. For exact-output swaps the caller cannot know the precise `amountIn` in advance, so they must send `msg.value ≥ amountInMaximum` as a buffer. The `pay` helper wraps only the exact amount the pool demands and leaves the remainder as raw ETH on the router. Neither function refunds that remainder. Because `refundETH()` is a public function that sweeps the router's entire ETH balance to `msg.sender` with no attribution check, any third party can call it in the same block and steal the stranded ETH.

---

### Finding Description

**Step 1 – ETH enters the router.**

`exactOutputSingle` is declared `payable`: [1](#0-0) 

The user sends `msg.value = amountInMaximum` (a common pattern when the exact input is unknown) and the call proceeds.

**Step 2 – Only the exact pool-demanded amount is consumed.**

During the swap callback, `_justPayCallback` calls `pay(WETH, payer, pool, amountIn)` where `amountIn` is the precise amount the pool requested — always `≤ amountInMaximum`: [2](#0-1) 

Inside `pay`, when `token == WETH` and `nativeBalance >= value`, exactly `value` wei is wrapped and forwarded; the rest of `address(this).balance` is untouched: [3](#0-2) 

**Step 3 – The function returns without refunding the surplus.**

After the swap, `exactOutputSingle` checks the slippage cap and clears transient state, but issues no ETH refund: [4](#0-3) 

The delta `msg.value − amountIn` remains as raw ETH on the router.

**Step 4 – Any caller can steal it via `refundETH()`.**

`refundETH()` is `external payable` with no access control; it transfers the router's entire ETH balance to `msg.sender`: [5](#0-4) 

A griever watching the mempool can front-run or back-run the victim's `exactOutputSingle` call with a `refundETH()` call and receive the stranded ETH.

The same root cause applies to `exactOutput` (multihop exact-output): [6](#0-5) 

---

### Impact Explanation

Any user who calls `exactOutputSingle` or `exactOutput` directly (not wrapped in a `multicall` + `refundETH` batch) with `tokenIn = WETH` and `msg.value > amountIn` loses the surplus ETH permanently. A third party can steal it in the same block with a single `refundETH()` call. The loss is bounded by `amountInMaximum − amountIn` per transaction, which can be substantial when the user sets a generous slippage buffer.

---

### Likelihood Explanation

The functions are `payable` and their signatures give no indication that callers must batch them with `refundETH`. Integrators and EOA users who follow the natural exact-output pattern — send a buffer, let the router use what it needs — will trigger the bug on every direct call. The theft requires only a public `refundETH()` call with no special privileges, making it trivially exploitable by any MEV bot.

---

### Recommendation

Add an automatic ETH refund at the end of `exactOutputSingle` and `exactOutput` (mirroring the fix in the referenced external report):

```solidity
// after _clearExpectedCallbackPool()
uint256 surplus = address(this).balance;
if (surplus > 0) _transferETH(msg.sender, surplus);
```

Alternatively, restrict `refundETH` to be callable only within a `multicall` context and document that exact-output ETH flows must always be batched with `refundETH`.

---

### Proof of Concept

```solidity
// Alice calls exactOutputSingle directly with a 2x buffer
uint256 amountIn = router.exactOutputSingle{value: 2 ether}(
    IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: 1_000,
        amountInMaximum: 2 ether,   // generous buffer
        recipient: alice,
        deadline: block.timestamp + 60,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// amountIn ≈ 1 ether; router now holds ≈ 1 ether surplus

// Bob (any address) steals it in the same block
vm.prank(bob);
router.refundETH();                 // bob receives alice's surplus ETH

assertEq(address(router).balance, 0);
assertGt(bob.balance, 0);           // bob profited from alice's overpayment
```

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
