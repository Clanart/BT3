### Title
`exactOutputSingle` and `exactOutput` Strand Excess Native ETH on the Router, Claimable by Any Caller via `refundETH()` — (File: `metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

`exactOutputSingle` and `exactOutput` are `payable` and accept native ETH for WETH-input swaps. The `pay()` helper wraps only the exact amount the pool requests, leaving `msg.value − actualAmountIn` ETH stranded on the router. Neither function calls `refundETH()` before returning. Because `refundETH()` is a public function that sends the router's entire ETH balance to `msg.sender` (not to the original depositor), any third party can call it in a subsequent transaction and steal the stranded ETH.

---

### Finding Description

**Root cause — `pay()` wraps only what the pool asks for:** [1](#0-0) 

When `token == WETH` and `nativeBalance >= value`, the function wraps exactly `value` wei and transfers it to the pool. The remaining `nativeBalance − value` ETH is left on the router with no attribution.

**Root cause — `exactOutputSingle` never refunds the surplus:** [2](#0-1) 

After the swap settles, the function checks `amountIn > amountInMaximum` and clears the callback context, but it never calls `refundETH()`. The same omission exists in `exactOutput`. [3](#0-2) 

**Root cause — `refundETH()` is public and sends to `msg.sender`, not the original depositor:** [4](#0-3) 

There is no access control and no record of who deposited the ETH. Any caller becomes the beneficiary.

---

### Impact Explanation

A user who calls `exactOutputSingle{value: amountInMaximum}(...)` directly (not via `multicall`) loses `amountInMaximum − actualAmountIn` ETH to the next caller of `refundETH()`. The loss is a direct, permanent reduction of the user's principal with no on-chain recovery path for the victim. The attacker's cost is a single cheap external call.

The same applies to `exactOutput` with a multi-hop WETH-input path.

---

### Likelihood Explanation

- `exactOutputSingle` and `exactOutput` are public `payable` entry points; nothing prevents direct calls with excess ETH.
- The intended multicall pattern (`[exactOutputSingle, refundETH]`) is documented only in test comments, not enforced on-chain. [5](#0-4) 

- Any integrator, SDK, or user who calls `exactOutputSingle` directly with a conservative `amountInMaximum` (the normal safe practice) triggers the vulnerability.
- A bot watching the mempool can front-run the victim's `refundETH()` call or simply call it in the next block.

---

### Recommendation

Add an automatic ETH refund at the end of `exactOutputSingle` and `exactOutput` when `tokenIn == WETH` and `address(this).balance > 0`:

```solidity
// inside exactOutputSingle, after _clearExpectedCallbackPool():
if (params.tokenIn == WETH) {
    uint256 surplus = address(this).balance;
    if (surplus > 0) _transferETH(msg.sender, surplus);
}
```

Apply the same pattern to `exactOutput`. This mirrors how Uniswap v3's `SwapRouter` handles the surplus in `exactOutputSingle` when `hasAlreadyPaid` is false.

Alternatively, document that these functions MUST NOT be called directly with ETH and enforce it with a revert when `msg.value > 0` outside of a `multicall` context — though the automatic refund is the safer user-facing fix.

---

### Proof of Concept

```
State: pool WETH/TOKEN1 exists, oracle live.

1. Alice calls:
   router.exactOutputSingle{value: 1 ether}(ExactOutputSingleParams({
       pool:            address(pool),
       tokenIn:         WETH,
       tokenOut:        TOKEN1,
       zeroForOne:      true,
       amountOut:       1_500,          // small exact output
       amountInMaximum: 1 ether,        // conservative cap
       recipient:       alice,
       deadline:        block.timestamp + 1,
       priceLimitX64:   0,
       extensionData:   ""
   }));

   Internally:
   - pool calls metricOmmSwapCallback → _justPayCallback → pay(WETH, alice, pool, actualAmountIn)
   - pay() sees nativeBalance (1 ETH) >= actualAmountIn (~1_600 wei at test prices)
   - Wraps actualAmountIn, transfers to pool. Leaves ~(1 ETH − 1_600 wei) on router.
   - exactOutputSingle returns. No refund.

2. Bob (any address) calls:
   router.refundETH();
   // refundETH sends address(this).balance to msg.sender (Bob)
   // Bob receives ~(1 ETH − 1_600 wei) — Alice's surplus ETH.

3. Alice's net loss: ~1 ETH − actualAmountIn (nearly the full msg.value).
```

The existing test `test_multicall_ethInput_exactInputSingle_refundsUnusedEth` confirms that excess ETH does remain on the router after a swap and must be explicitly reclaimed — but no equivalent guard exists inside `exactOutputSingle` itself. [6](#0-5)

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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
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

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L8-10)
```text
/// @dev Native ETH flows follow Uniswap v3-periphery multicall patterns:
///      - ETH input: multicall{value}(exactInput*) with WETH as tokenIn
///      - ETH output: swap WETH to router, then unwrapWETH9 in the same multicall
```

**File:** metric-periphery/test/MetricOmmSimpleRouter.native.t.sol (L106-133)
```text
  function test_multicall_ethInput_exactInputSingle_refundsUnusedEth() public {
    uint128 amountIn = 1_000;
    uint256 msgValue = 2 ether;
    uint256 swapperEthBefore = swapper.balance;

    vm.prank(swapper);
    bytes[] memory calls = new bytes[](2);
    calls[0] = abi.encodeWithSelector(
      router.exactInputSingle.selector,
      IMetricOmmSimpleRouter.ExactInputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountIn: amountIn,
        amountOutMinimum: 0,
        recipient: recipient,
        deadline: _deadline(),
        priceLimitX64: 0,
        extensionData: ""
      })
    );
    calls[1] = abi.encodeWithSelector(router.refundETH.selector);
    router.multicall{value: msgValue}(calls);

    assertEq(swapper.balance, swapperEthBefore - amountIn, "unused eth refunded");
    _assertRouterEmpty();
  }
```
