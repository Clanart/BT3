### Title
Native ETH Lockup in `exactInputSingle` When Pool Partially Fills Due to Price Limit — (`metric-periphery/contracts/MetricOmmSimpleRouter.sol`)

---

### Summary

`MetricOmmSimpleRouter.exactInputSingle` is `payable` and accepts native ETH as the swap input. When the pool partially fills (price limit reached before all input is consumed), the swap callback `_justPayCallback` wraps and forwards only the amount the pool actually consumed. The remaining ETH is silently retained in the router with no automatic refund, permanently locking user funds.

---

### Finding Description

`exactInputSingle` accepts `msg.value` ETH from the caller and stores `msg.sender` as the payer in transient callback context: [1](#0-0) 

During the pool's `swap` call, the pool invokes `metricOmmSwapCallback`, which dispatches to `_justPayCallback`: [2](#0-1) 

`_justPayCallback` calls `pay()` with `extractPositiveAmount(amount0Delta, amount1Delta)` — the amount the pool actually consumed, not `msg.value`. If the pool hit `priceLimitX64` before consuming all `params.amountIn`, the callback wraps and transfers only the consumed portion. The surplus ETH (`msg.value − amountConsumed`) remains in the router contract.

After the swap, `exactInputSingle` only checks `amountOut < params.amountOutMinimum` and clears the callback pool: [3](#0-2) 

There is no `refundETH()` call, no surplus-ETH check, and no revert on partial fill. The function returns successfully with the partial output, and the unspent ETH is stranded.

**Contrast with `exactInput` (multi-hop):** that function explicitly reverts on partial fill at every hop: [4](#0-3) 

This asymmetry confirms that partial fills are a recognized possibility in the pool, and that `exactInputSingle` lacks the equivalent guard.

---

### Impact Explanation

A user who calls `exactInputSingle` with native ETH and a non-open `priceLimitX64` (or whose swap is front-run to drain pool liquidity before execution) will have the unconsumed ETH permanently locked in the router. The loss is proportional to `msg.value − amountActuallyConsumed`. Because the router is not a custodian and has no owner-controlled sweep for ETH, recovery requires a public `refundETH`-style function (not confirmed present) callable by anyone — meaning a third party could drain the stranded ETH before the victim.

**Severity: Medium** — direct loss of user principal; requires a price-limit partial fill condition, which is a normal and expected operational scenario.

---

### Likelihood Explanation

- Any user who sets a non-trivial `priceLimitX64` (e.g., to bound slippage) and whose swap is partially filled triggers the bug.
- A sandwich attacker can drain pool liquidity before the victim's transaction, causing a partial fill, then recover the stranded ETH via `refundETH` (if public) in the same block.
- The `multicall` pattern (inherited by the router) is the standard workaround, but `exactInputSingle` is a standalone `external payable` function that users call directly without bundling a refund call.

---

### Recommendation

Add an explicit partial-fill guard in `exactInputSingle` analogous to the one in `exactInput`:

```solidity
int128 amountInActual = MetricOmmSwapResults.extractAmountIn(
    params.zeroForOne, amount0Delta, amount1Delta
);
if (uint128(amountInActual) < params.amountIn)
    revert PartialFill(uint128(amountInActual), params.amountIn);
```

Alternatively, unconditionally refund surplus ETH to `params.recipient` (or `msg.sender`) at the end of `exactInputSingle` using the `PeripheryPayments` refund primitive, mirroring the Uniswap V3 periphery pattern where `refundETH()` is called inside the same multicall bundle.

---

### Proof of Concept

1. Pool `P` has WETH/TokenB with limited liquidity at the current oracle price.
2. Attacker front-runs victim's `exactInputSingle` call, consuming most of the pool's liquidity.
3. Victim's `exactInputSingle` executes with `msg.value = 1 ETH`, `priceLimitX64 = X` (non-open).
4. Pool partially fills: consumes 0.1 ETH, hits price limit, returns.
5. `_justPayCallback` wraps 0.1 ETH and sends WETH to pool. 0.9 ETH remains in router.
6. `amountOut` passes `amountOutMinimum` check (if victim set it low or zero).
7. `exactInputSingle` returns successfully. Victim lost 0.9 ETH.
8. Attacker (or anyone) calls `refundETH()` on the router in the next transaction and receives the 0.9 ETH. [1](#0-0) [2](#0-1)

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
