### Title
`exactOutputSingle` and `exactOutput` Do Not Refund Excess Native ETH, Enabling Theft via `refundETH()` — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

`exactOutputSingle` and `exactOutput` are `payable` and accept native ETH as WETH input. The `pay` helper in `PeripheryPayments` consumes only the exact `amountIn` determined by the pool, leaving any `msg.value` surplus in the router. Neither function calls `refundETH()` before returning. Because `refundETH()` sends the entire ETH balance to its own `msg.sender`, any third party can immediately steal the stranded ETH.

---

### Finding Description

The `pay` function handles native-ETH-backed WETH payments with the following branch:

```solidity
// PeripheryPayments.sol L75-77
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
}
```

Only `value` (the pool-determined `amountIn`) is deposited and forwarded; `address(this).balance - value` remains in the contract. [1](#0-0) 

For exact-output swaps the caller cannot know `amountIn` in advance; they must send up to `amountInMaximum` ETH. After the swap settles, `exactOutputSingle` returns without issuing a refund:

```solidity
// MetricOmmSimpleRouter.sol L145-146
if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
_clearExpectedCallbackPool();
// ← no refundETH() here
``` [2](#0-1) 

`exactOutput` has the identical omission: [3](#0-2) 

`refundETH()` sends the **entire** ETH balance of the router to whoever calls it:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
``` [4](#0-3) 

There is no access control on `refundETH()` and no binding of the refund to the original depositor. [5](#0-4) 

---

### Impact Explanation

A user who calls `exactOutputSingle{value: X}(...)` or `exactOutput{value: X}(...)` with `X > amountIn` loses `X - amountIn` ETH to any address that calls `refundETH()` in the same or a subsequent block. The loss is direct, concrete, and proportional to the ETH overshoot. Because exact-output swaps by definition require the caller to over-provision ETH (the precise `amountIn` is unknown until execution), every native-ETH exact-output call that is not wrapped in a `multicall` + `refundETH()` bundle is vulnerable.

---

### Likelihood Explanation

- The functions are `payable` and the mixed native-ETH/WETH path is explicitly tested and documented as a supported flow.
- Wallets, aggregators, and integrators that call `exactOutputSingle` or `exactOutput` directly with ETH (without `multicall`) will leave stranded ETH on every call.
- A front-running bot needs only to watch the mempool for such calls and append a `refundETH()` call immediately after, or simply call it in the next block.
- No privileged access, no special setup, and no non-standard token behavior is required.

---

### Recommendation

Add an automatic ETH refund at the end of both exact-output entry points when `tokenIn == WETH`:

```solidity
function exactOutputSingle(ExactOutputSingleParams calldata params) external payable returns (uint256 amountIn) {
    // ... existing logic ...
    _clearExpectedCallbackPool();
    // Refund any unused native ETH back to msg.sender
    if (address(this).balance > 0) _transferETH(msg.sender, address(this).balance);
}
```

Apply the same fix to `exactOutput`. Alternatively, document clearly that these functions **must** be called via `multicall` with a trailing `refundETH()` call whenever native ETH is supplied, and add a guard that reverts if `msg.value > 0` and `tokenIn != WETH`.

---

### Proof of Concept

```
Setup:
  - WETH/token1 pool exists with bid/ask such that 1 500 token1 costs ~1 700 WETH units.
  - Alice wants exactly 1 500 token1 and sends 2 000 ETH as amountInMaximum.

Step 1: Alice calls
  router.exactOutputSingle{value: 2000}(ExactOutputSingleParams{
      tokenIn: WETH, amountOut: 1500, amountInMaximum: 2000, ...
  });

Step 2: Inside the swap callback, pay() is invoked with value = 1 700.
  nativeBalance (2 000) >= value (1 700) → deposits 1 700 ETH as WETH, forwards to pool.
  300 ETH remains in router.

Step 3: exactOutputSingle returns amountIn = 1 700. No refund is issued.
  address(router).balance == 300.

Step 4: Bob (front-runner or any EOA) calls router.refundETH().
  refundETH sends 300 ETH to Bob.

Result: Alice loses 300 ETH; Bob gains 300 ETH. Alice received her 1 500 token1 but paid
        2 000 ETH instead of 1 700 ETH.
```

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

**File:** metric-periphery/contracts/interfaces/IPeripheryPayments.sol (L18-19)
```text
  /// @notice Refund all ETH held by this contract to `msg.sender`.
  function refundETH() external payable;
```
