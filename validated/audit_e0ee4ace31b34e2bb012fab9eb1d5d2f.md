Audit Report

## Title
Surplus native ETH stranded on router after `exactOutputSingle`/`exactOutput` is stealable by any caller via `refundETH()` — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol` / `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`exactOutputSingle` and `exactOutput` are `payable` and accept native ETH as a WETH substitute. The internal `pay()` helper deposits only the exact pool-requested `amountIn`, leaving any `msg.value` surplus on the router with no automatic refund. `refundETH()` is an unconditional public function that sweeps the entire router ETH balance to `msg.sender`, allowing any third party to steal the stranded surplus in a follow-up call.

## Finding Description

`PeripheryPayments.pay()` handles WETH-input swaps with native ETH: [1](#0-0) 

When `nativeBalance >= value`, exactly `value` ETH is deposited and `nativeBalance - value` remains on the router with no refund path.

`exactOutputSingle` is `payable` and terminates without refunding surplus `msg.value`: [2](#0-1) 

The function computes `amountIn` from the pool's actual deltas and enforces `amountIn <= amountInMaximum`, but never issues `msg.value - amountIn` back to the caller. The identical omission exists in `exactOutput`: [3](#0-2) 

`refundETH()` is unconditionally public and sends the full router ETH balance to `msg.sender`: [4](#0-3) 

There is no access control, no record of who deposited the ETH, and no linkage to the original caller. Any address that calls `refundETH()` after Alice's transaction receives Alice's stranded ETH.

Note: the `receive()` guard (line 32–34) only restricts plain ETH transfers, not `msg.value` attached to payable function calls, so it provides no protection here. [5](#0-4) 

## Impact Explanation

Direct, unprivileged loss of user ETH principal. A caller who sends `msg.value = amountInMaximum` (the standard UX pattern for exact-output swaps, since the exact cost is unknown pre-execution) loses `msg.value - actual amountIn`. The surplus is immediately claimable by any address. No recovery path exists once the transaction is mined. Severity: **High** — direct loss of user funds, no privilege required.

## Likelihood Explanation

The standard pattern for exact-output swaps is to send `msg.value` equal to an upper-bound estimate because the exact input is unknown before execution. A direct call to `exactOutputSingle` with any surplus ETH — the natural usage — silently strands funds. The theft requires only a mempool observer and a single follow-up `refundETH()` call, making it trivially exploitable with no time window constraint.

## Recommendation

Add an automatic ETH refund at the end of `exactOutputSingle` and `exactOutput` after `_clearExpectedCallbackPool()`:

```solidity
uint256 surplus = address(this).balance;
if (surplus > 0) {
    _transferETH(msg.sender, surplus);
}
```

This mirrors the fix applied in analogous router implementations. Alternatively, require callers to use `multicall([exactOutputSingle(...), refundETH()])`, but this must be enforced at the contract level (e.g., by making the functions non-`payable` outside `multicall`), not merely documented.

## Proof of Concept

```
1. Alice calls:
       router.exactOutputSingle{value: 2 ether}(ExactOutputSingleParams({
           tokenIn: WETH, amountOut: 1_500, amountInMaximum: 2 ether, ...
       }));
2. Inside the swap callback, pay() is invoked with value = ~1 ether (actual amountIn).
   nativeBalance = 2 ether >= 1 ether → deposits exactly 1 ether, transfers WETH to pool.
   Remaining 1 ether stays on the router (PeripheryPayments.sol line 75-77).
3. exactOutputSingle returns at line 146 with no refund.
4. Bob (mempool watcher) calls router.refundETH().
   refundETH() (line 61) sends address(router).balance = 1 ether to Bob.
5. Alice loses 1 ether; Bob gains 1 ether. Alice received her output tokens but paid 2 ether instead of ~1 ether.
```

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
