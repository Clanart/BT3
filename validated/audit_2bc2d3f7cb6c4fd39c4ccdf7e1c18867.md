Audit Report

## Title
Excess ETH sent with `exactInputSingle`/`exactInput`/`exactOutputSingle`/`exactOutput` is stranded in the router and claimable by any caller via `refundETH()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
When a user calls `exactInputSingle` (or any payable swap function) with `tokenIn == WETH` and `msg.value > amountIn`, the `pay()` function wraps only the required `amountIn` worth of ETH and forwards it to the pool. The surplus ETH is left in the router with no automatic refund and no ownership record. Any address can then call the permissionless `refundETH()` to drain the entire router ETH balance, stealing the user's surplus.

## Finding Description
`exactInputSingle` is `payable` and accepts arbitrary ETH: [1](#0-0) 

The call chain is: `exactInputSingle` → `pool.swap()` → `metricOmmSwapCallback` → `_justPayCallback` → `pay()`. Inside `pay()`, the WETH branch wraps exactly `value` (= `amountIn`) when `nativeBalance >= value`: [2](#0-1) 

Any ETH above `amountIn` is not consumed. After the swap returns, `exactInputSingle` performs no ETH refund — it only checks `amountOutMinimum` and clears the callback context: [3](#0-2) 

`refundETH()` is fully permissionless — it sends `address(this).balance` to `msg.sender` with no ownership check: [4](#0-3) 

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks direct ETH transfers; it does not apply to ETH arriving through a `payable` function call such as `exactInputSingle`: [5](#0-4) 

The same stranding applies to `exactInput` (first hop, `i==0`, payer=`msg.sender`, `tokenIn==WETH`), `exactOutputSingle`, and `exactOutput`. [6](#0-5) 

## Impact Explanation
Direct loss of user ETH principal. A user sending `msg.value = 1e18` with `amountIn = 0.5e18` loses 0.5 ETH to any caller of `refundETH()`. Loss scales linearly with surplus. This meets the Critical/High direct-loss-of-user-principal threshold.

## Likelihood Explanation
Medium. Users who interact directly with the router without bundling `refundETH()` in a `multicall` are exposed. Frontends that omit the refund step produce vulnerable transactions. MEV bots already monitor routers for stranded ETH and can extract the surplus in the same block.

## Recommendation
Add an automatic ETH refund at the end of each payable swap function:

```solidity
uint256 surplus = address(this).balance;
if (surplus > 0) _transferETH(msg.sender, surplus);
```

Alternatively, when `tokenIn == WETH`, enforce `msg.value == amountIn` and revert otherwise, eliminating the ambiguity entirely.

## Proof of Concept
1. User calls `exactInputSingle({tokenIn: WETH, amountIn: 0.5e18, ...})` with `msg.value = 1e18`.
2. `pay()` wraps 0.5e18 ETH → sends WETH to pool. Router now holds 0.5e18 ETH.
3. `exactInputSingle` returns. No refund issued.
4. Attacker calls `refundETH()`. Router sends 0.5e18 ETH to attacker.
5. User's 0.5e18 ETH is permanently lost.

### Citations

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L67-67)
```text
  function exactInputSingle(ExactInputSingleParams calldata params) external payable returns (uint256 amountOut) {
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L81-85)
```text
    int128 out = MetricOmmSwapResults.extractAmountOut(params.zeroForOne, amount0Delta, amount1Delta);
    amountOut = MetricOmmSwapInputs.int128ToUint128(out);
    if (amountOut < params.amountOutMinimum) revert InsufficientOutput(amountOut, params.amountOutMinimum);

    _clearExpectedCallbackPool();
```

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L99-103)
```text
    for (uint256 i = 0; i <= last; i++) {
      address pool = params.pools[i];
      bool zeroForOne = MetricOmmSwapPath.resolveZeroForOneBitmap(params.zeroForOneBitMap, i);

      _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, i == 0 ? msg.sender : address(this), params.tokens[i]);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
```
