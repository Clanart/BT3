The code confirms all claims. Let me verify the key facts:

1. All four swap entry-points are `payable` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

2. `pay()` wraps only `value`, not `nativeBalance`, leaving any surplus in the router [5](#0-4) 

3. `refundETH()` is unrestricted — any caller receives the full ETH balance [6](#0-5) 

4. `receive()` only blocks plain ETH transfers from non-WETH addresses; it does not block ETH attached to a function call [7](#0-6) 

5. No swap function performs an automatic post-swap ETH refund to `msg.sender` [8](#0-7) 

All preconditions, code paths, and guards are confirmed against the production code.

---

Audit Report

## Title
Excess native ETH sent to payable swap functions is permanently stranded and can be stolen via `refundETH()` - (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
Every swap entry-point on `MetricOmmSimpleRouter` is `payable`, but `pay()` wraps only the exact amount of ETH the pool requests, leaving any surplus attached by the caller sitting in the router. `refundETH()` is a public, unrestricted function that forwards the router's entire ETH balance to `msg.sender`, so any third party can claim that surplus before the original caller reclaims it.

## Finding Description
When a user calls `exactInputSingle{value: X}(params)` with `tokenIn == WETH` and `X > amountIn`, the swap callback chain reaches `_justPayCallback` → `pay(WETH, payer, pool, value)`. Inside `pay()`, the branch at `PeripheryPayments.sol:75` is:

```solidity
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();   // wraps only `value`
    IERC20(WETH).safeTransfer(recipient, value);
}
```

The condition accepts any surplus; only `value` is consumed. The remainder `nativeBalance - value` stays in the router with no automatic refund. None of the four swap functions (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`) perform a post-swap ETH refund to `msg.sender`.

`refundETH()` at `PeripheryPayments.sol:58-63` is `external` with no access control:

```solidity
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);
    }
}
```

The `receive()` guard at `PeripheryPayments.sol:32-34` only reverts plain ETH transfers from non-WETH addresses; it does not prevent ETH from being attached to a function call, so the user's excess ETH enters the contract normally. Once the swap settles, any address can call `refundETH()` and drain the surplus.

## Impact Explanation
Direct, permanent loss of user ETH principal. The surplus is not locked — it is immediately claimable by any third party via `refundETH()`. This meets the "direct loss of user principal" criterion. Severity: High.

## Likelihood Explanation
Requires the user to attach more ETH than the swap consumes. This occurs via buggy integrations, slippage miscalculations, or a user manually over-funding a WETH-input swap. The safe path (multicall batching `exactInputSingle` + `refundETH`) is not enforced, and a direct single call with excess ETH is a realistic mistake. Likelihood: Low. Combined rating: Medium (Low likelihood × High impact).

## Recommendation
Add an automatic ETH refund at the end of each payable swap function:

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
1. Pool has a WETH/USDC pair; `amountIn` for the swap is `1 ETH`.
2. User calls `exactInputSingle{value: 2 ether}(params)` with `tokenIn = WETH`.
3. Pool triggers `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, user, pool, 1e18)`.
4. `pay()` sees `nativeBalance = 2e18 >= 1e18 = value`, wraps `1e18`, transfers to pool. `1e18` ETH remains in router.
5. Attacker observes the transaction (mempool or post-inclusion) and calls `refundETH()`.
6. `refundETH()` sends the full `1e18` ETH balance to the attacker.
7. User receives correct swap output but loses `1 ETH` of excess input with no recovery path.

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
