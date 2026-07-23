Audit Report

## Title
`exactOutputSingle` and `exactOutput` strand excess native ETH on the router, claimable by any third party — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

## Summary

When a user calls `exactOutputSingle` or `exactOutput` with native ETH as the input token (WETH path), the `pay()` helper wraps and forwards only the actual `amountIn` to the pool. Any excess ETH (`msg.value − amountIn`) remains on the router with no automatic refund. Because `refundETH()` is a public, access-control-free function that sends the entire router ETH balance to `msg.sender`, any third party can drain the stranded ETH in a subsequent transaction, causing a direct, unconditional loss of user principal.

## Finding Description

**Root cause — `exactOutputSingle` (L130–147):**

`exactOutputSingle` is `payable` and accepts arbitrary `msg.value`. After the pool swap settles, `_justPayCallback` calls `pay(WETH, originalCaller, pool, amountIn)` where `amountIn` is the actual cost determined by the pool — not `msg.value`. Inside `pay()`, the WETH branch wraps only `value` (the actual cost):

```solidity
// PeripheryPayments.sol L74-77
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // wraps only amountIn, not msg.value
    IERC20(WETH).safeTransfer(recipient, value);
```

After `pay()` returns, `exactOutputSingle` performs the slippage check and calls `_clearExpectedCallbackPool()` — **no ETH refund is issued**:

```solidity
// MetricOmmSimpleRouter.sol L145-147
if (amountIn > params.amountInMaximum) revert InputTooHigh(amountIn, params.amountInMaximum);
_clearExpectedCallbackPool();
// function returns — excess ETH stays on router
```

The same pattern applies to `exactOutput` (L154–188), which also ends without refunding ETH.

**Draining vector — `refundETH()` (L58–63):**

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // sends full balance to any caller
    }
}
```

There is no caller identity check. Any address that calls `refundETH()` after a victim's `exactOutputSingle` transaction receives the entire stranded ETH balance.

**`receive()` guard is irrelevant here (L32–34):**

```solidity
receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
}
```

This only blocks direct ETH deposits. It does not prevent `msg.value` from accumulating via `payable` swap functions, so it provides no protection against this attack.

**Exploit flow:**
1. User calls `exactOutputSingle{value: 2 ETH}(...)` with `amountInMaximum = 2 ETH`.
2. Pool settles at `amountIn = 1 ETH`; `pay()` wraps and forwards 1 ETH to pool.
3. `exactOutputSingle` returns; 1 ETH excess remains on router.
4. Attacker calls `router.refundETH()` in a subsequent transaction and receives 1 ETH.

## Impact Explanation

This is a direct, unconditional loss of user principal. Any user who calls `exactOutputSingle` or `exactOutput` directly (not via a `multicall` that also includes `refundETH()`) with `msg.value > actual amountIn` permanently loses the difference. The excess is immediately claimable by any third party with no preconditions. This meets the Critical/High threshold for direct loss of user funds.

## Likelihood Explanation

Exact-output swaps are specifically designed for users who want a guaranteed output and set `amountInMaximum` as a slippage cap above the expected cost. This is the standard usage pattern. Any user calling `exactOutputSingle` or `exactOutput` directly (without `multicall`) will trigger the vulnerability. No special attacker capability is required — calling `refundETH()` is permissionless and costs only gas. The attack is repeatable on every such transaction.

## Recommendation

Add an automatic ETH refund at the end of `exactOutputSingle` and `exactOutput` after `amountIn` is known:

```solidity
// After the amountInMaximum check in exactOutputSingle / exactOutput:
uint256 excess = address(this).balance;
if (excess > 0) {
    _transferETH(msg.sender, excess);
}
```

Alternatively, document prominently in NatSpec that callers **must** wrap calls in `multicall` with `refundETH()` when sending native ETH, and add a test asserting no ETH remains on the router after a standalone `exactOutputSingle` call with excess `msg.value`.

## Proof of Concept

```solidity
// 1. User calls exactOutputSingle directly with excess ETH:
uint256 amountIn = router.exactOutputSingle{value: 2 ether}(
    IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: 1500,
        amountInMaximum: uint128(2 ether),
        recipient: user,
        deadline: block.timestamp + 60,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// amountIn == ~1 ether; 1 ether excess stranded on router

// 2. Attacker drains in a subsequent tx:
router.refundETH();
// Attacker receives 1 ether; user's excess is gone
assert(address(attacker).balance == 1 ether);
assert(address(router).balance == 0);
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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
