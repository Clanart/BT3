The code confirms the claim. Key findings:

1. `exactInputSingle` is `payable` with no ETH refund after the swap. [1](#0-0) 
2. `_justPayCallback` wraps only the consumed amount (`extractPositiveAmount`), leaving surplus ETH in the router. [2](#0-1) 
3. `pay()` confirms: when `token == WETH` and `nativeBalance >= value`, it wraps exactly `value` (consumed), not `msg.value`. [3](#0-2) 
4. `refundETH()` is `external payable` — callable by any address, not just the original sender. [4](#0-3) 
5. `exactInput` has the partial-fill guard; `exactInputSingle` does not. [5](#0-4) 

---

Audit Report

## Title
Native ETH Stranded in Router on Partial Fill in `exactInputSingle` — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

## Summary
`exactInputSingle` is `payable` and accepts native ETH as swap input. When the pool partially fills due to a price limit, `_justPayCallback` wraps and forwards only the consumed ETH to the pool; the surplus remains in the router. No refund is issued, and the publicly callable `refundETH()` allows any third party to claim the stranded ETH before the victim.

## Finding Description
`exactInputSingle` stores `msg.sender` as payer and calls `pool.swap(...)` with `params.amountIn` and a caller-supplied `priceLimitX64`. If the pool hits the price limit before consuming all input, the swap returns with `|amount0Delta|` or `|amount1Delta|` less than `params.amountIn`. The callback `_justPayCallback` calls `pay()` with `extractPositiveAmount(amount0Delta, amount1Delta)` — the actually consumed amount — not `msg.value`. Inside `pay()`, when `token == WETH`, the branch at line 75–77 wraps exactly `value` (consumed) and transfers it; the remaining `msg.value − consumed` ETH stays in the contract. After the swap, `exactInputSingle` only checks `amountOut < params.amountOutMinimum` and calls `_clearExpectedCallbackPool()` — no refund, no partial-fill revert. The function returns successfully. `refundETH()` is `external payable` with no access control, so any caller can immediately drain the stranded ETH.

By contrast, `exactInput` explicitly reverts on partial fill at every hop (`if (amountInActual < amount) revert InvalidInputAmountAtHop(...)`), confirming partial fills are a recognized pool behavior that `exactInputSingle` fails to guard against.

## Impact Explanation
Direct loss of user principal proportional to `msg.value − amountActuallyConsumed`. The stranded ETH is immediately claimable by any third party via the public `refundETH()`. This meets the Medium threshold for direct loss of user funds through a normal operational scenario (price-limit partial fill).

## Likelihood Explanation
Any user who sets a non-open `priceLimitX64` and whose swap is partially filled triggers the bug. A sandwich attacker can front-run the victim to drain pool liquidity, causing a partial fill, then call `refundETH()` in the same block to recover the stranded ETH. The `multicall` workaround (bundling `exactInputSingle` + `refundETH`) is not enforced by the contract and is not the documented interface for `exactInputSingle` as a standalone `external payable` function.

## Recommendation
Add a partial-fill guard in `exactInputSingle` analogous to `exactInput`:

```solidity
int128 amountInActual = MetricOmmSwapResults.extractAmountIn(
    params.zeroForOne, amount0Delta, amount1Delta
);
if (uint128(amountInActual) < params.amountIn)
    revert PartialFill(uint128(amountInActual), params.amountIn);
```

Alternatively, unconditionally refund surplus ETH to `msg.sender` at the end of `exactInputSingle` using `_transferETH` or a dedicated internal refund helper, mirroring the Uniswap V3 periphery pattern.

## Proof of Concept
1. Deploy pool P (WETH/TokenB) with limited liquidity at current price.
2. Attacker front-runs victim's `exactInputSingle` call, consuming most pool liquidity.
3. Victim calls `exactInputSingle` with `msg.value = 1 ETH`, `priceLimitX64 = X` (non-open), `amountOutMinimum = 0`.
4. Pool partially fills: consumes 0.1 ETH, hits price limit, returns `amount0Delta`/`amount1Delta` reflecting 0.1 ETH consumed.
5. `_justPayCallback` → `pay()` wraps 0.1 ETH and sends WETH to pool. 0.9 ETH remains in router.
6. `amountOut` passes the `amountOutMinimum = 0` check; `exactInputSingle` returns successfully.
7. Attacker calls `refundETH()` on the router and receives 0.9 ETH. Victim lost 0.9 ETH.

A Foundry integration test can reproduce this by: deploying the router and a mock pool that returns partial deltas when price limit is hit, calling `exactInputSingle{value: 1 ether}(...)`, asserting `address(router).balance == 0.9 ether`, then calling `refundETH()` from a different address and asserting that address received 0.9 ETH.

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

**File:** metric-periphery/contracts/MetricOmmSimpleRouter.sol (L114-115)
```text
      int128 amountInActual = MetricOmmSwapResults.extractAmountIn(zeroForOne, amount0Delta, amount1Delta);
      if (amountInActual < amount) revert InvalidInputAmountAtHop(uint8(i), amountInActual, amount);
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

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L73-78)
```text
    } else if (token == WETH) {
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
      } else if (nativeBalance > 0) {
```
