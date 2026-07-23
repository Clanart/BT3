### Title
Excess native ETH sent to `exactOutputSingle`/`exactOutput` is not refunded and is stealable by any caller via `refundETH()` - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

### Summary
When a user calls `exactOutputSingle` or `exactOutput` with `tokenIn = WETH` and sends more ETH than the actual `amountIn`, the `pay()` function deposits exactly `amountIn` ETH and leaves the remainder in the router. Neither swap function automatically refunds this excess. Because `refundETH()` transfers the router's entire ETH balance to `msg.sender` with no attribution, any third party can call it in a subsequent transaction and steal the stranded ETH. The same stranded ETH can also be silently consumed by a later caller's swap at zero cost to that caller.

### Finding Description
`pay()` in `PeripheryPayments` reads `address(this).balance` at callback time and, when that balance covers the required `value`, deposits exactly `value` ETH into WETH and forwards it to the pool — leaving any surplus native ETH sitting in the router:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);   // payer is never touched
}
``` [1](#0-0) 

Neither `exactOutputSingle` nor `exactOutput` calls `refundETH()` or performs any post-swap ETH accounting before returning: [2](#0-1) [3](#0-2) 

`refundETH()` is a public, unauthenticated function that sends the router's full ETH balance to whoever calls it:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // no attribution check
    }
}
``` [4](#0-3) 

The `receive()` guard only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` on `payable` swap functions. [5](#0-4) 

A second theft vector exists independently: because `pay()` uses `address(this).balance` rather than `msg.value`, a subsequent caller who sends `msg.value = 0` but specifies `tokenIn = WETH` will have their entire swap obligation silently covered by the stranded ETH — receiving a free swap at the original user's expense.

### Impact Explanation
Direct loss of user-supplied native ETH. Any ETH sent above the exact `amountIn` required by the pool is permanently stranded in the router after the swap completes. It is immediately claimable by any address via `refundETH()`, or silently consumed by the next WETH-input swap. The original sender has no atomic or privileged path to recover it once the swap transaction is mined. Loss magnitude equals `msg.value − amountIn`, which can be arbitrarily large depending on how much the user over-sent (e.g., UI slippage buffer, gas estimation rounding, or a deliberate over-send to guarantee the swap succeeds).

### Likelihood Explanation
Medium. The intended usage pattern (documented in the test file) is `multicall{value}(exactOutput*, refundETH)`, which atomically refunds the excess. However, `exactOutputSingle` and `exactOutput` are independently `payable` and callable without `multicall`. Any integrator, wallet, or user who calls them directly with a conservative ETH over-send — a common defensive pattern — will strand the excess. The stranded ETH is then immediately visible on-chain and claimable by a bot or frontrunner in the same block.

### Recommendation
Add an automatic refund of any remaining native ETH balance at the end of `exactOutputSingle` and `exactOutput`, mirroring the pattern the test suite already validates for `exactInputSingle`:

```solidity
// at the end of exactOutputSingle and exactOutput, after all state changes:
uint256 ethLeft = address(this).balance;
if (ethLeft > 0) {
    _transferETH(msg.sender, ethLeft);
}
```

Alternatively, enforce that `msg.value` must equal zero when `tokenIn != WETH`, and must equal exactly the quoted `amountIn` when `tokenIn == WETH`, so over-sends revert rather than strand funds.

### Proof of Concept

**Scenario A — frontrunner steals via `refundETH()`:**

1. Pool state: `amountIn` for a given exact-output swap is `1 ETH`.
2. User calls `exactOutputSingle{value: 2 ETH}(tokenIn=WETH, amountOut=X, amountInMaximum=3 ETH, ...)`.
3. Inside the swap callback, `pay()` reads `nativeBalance = 2 ETH`, deposits exactly `1 ETH` into WETH, and forwards it to the pool. The remaining `1 ETH` stays in the router.
4. `exactOutputSingle` returns. The router holds `1 ETH` with no owner record.
5. A frontrunner observes the pending or mined transaction and calls `router.refundETH()`.
6. `refundETH()` transfers the full `1 ETH` balance to the frontrunner's address.
7. User receives the correct swap output but loses `1 ETH` of overpayment permanently.

**Scenario B — subsequent caller gets a free swap:**

1. Same setup: `1 ETH` is stranded in the router after User A's swap.
2. User B calls `exactOutputSingle{value: 0}(tokenIn=WETH, amountOut=Y, amountInMaximum=1 ETH, ...)` where the pool requires `1 ETH` input.
3. Inside the callback, `pay()` reads `nativeBalance = 1 ETH >= value = 1 ETH`, deposits the router's `1 ETH`, and forwards WETH to the pool — without pulling anything from User B.
4. User B receives swap output `Y` at zero cost. User A's stranded ETH is consumed.

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
