### Title
Unattributed Native ETH Stranded on Router Enables Cross-Caller Theft via `pay()` — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

`PeripheryPayments.pay()` uses the router's entire `address(this).balance` to fund WETH payments without any per-caller attribution. Every `payable` swap and liquidity entry-point (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `addLiquidityExactShares`, `addLiquidityWeighted`) can receive ETH via `msg.value`. When `msg.value` exceeds the amount actually consumed, the surplus ETH is silently stranded on the router. Any subsequent caller whose WETH payment is routed through `pay()` will have that surplus consumed on their behalf, effectively stealing the victim's ETH.

---

### Finding Description

`PeripheryPayments.pay()` contains the following logic for WETH payments:

```solidity
// metric-periphery/contracts/base/PeripheryPayments.sol  lines 73-84
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
}
```

When `nativeBalance >= value`, the function wraps exactly `value` ETH and sends it to the pool. The remainder (`nativeBalance - value`) is left on the router with no record of which caller it belongs to.

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks plain ETH transfers. It does **not** block ETH arriving via `msg.value` in a `payable` function call. All six public entry-points are declared `payable`:

```solidity
// MetricOmmSimpleRouter.sol lines 67, 92, 130, 154
function exactInputSingle(ExactInputSingleParams calldata params) external payable ...
function exactInput(ExactInputParams calldata params) external payable ...
function exactOutputSingle(ExactOutputSingleParams calldata params) external payable ...
function exactOutput(ExactOutputParams calldata params) external payable ...

// MetricOmmPoolLiquidityAdder.sol lines 56, 71, 88, 123
function addLiquidityExactShares(...) external payable ...
function addLiquidityWeighted(...) external payable ...
```

None of these functions validate that `msg.value` equals the amount actually consumed. The intended safe pattern is `multicall{value: X}([swap(...), refundETH()])`, but calling any entry-point directly with excess ETH silently strands the surplus.

The most impactful trigger is `exactOutputSingle{value: amountInMaximum}`: the user must over-provision ETH because the exact `amountIn` is unknown before execution. The pool determines the actual `amountIn ≤ amountInMaximum`; `pay()` wraps only `amountIn`, leaving `amountInMaximum - amountIn` on the router.

---

### Impact Explanation

**High — direct loss of user principal.**

The stranded ETH is immediately claimable by any subsequent caller who triggers a WETH payment through `pay()`. The attacker calls `exactInputSingle{value: 0}` with `tokenIn = WETH` and `amountIn = <stranded amount>`. Inside the callback, `pay(WETH, attacker, pool, stranded)` finds `address(this).balance == stranded`, wraps it, and sends it to the pool — the attacker receives the swap output funded entirely by the victim's ETH. The victim loses the full surplus with no recourse.

---

### Likelihood Explanation

**Medium.** The `exactOutputSingle` and `exactOutput` patterns inherently require the caller to over-provision ETH when paying natively, because the exact input is unknown before execution. Any user who calls these functions directly with `msg.value = amountInMaximum` (a natural and documented usage pattern) triggers the vulnerability. A MEV bot monitoring the mempool can front-run or back-run to claim the stranded ETH in the same block.

---

### Recommendation

1. **Enforce `msg.value` accounting at entry-points.** After each swap or liquidity operation, assert `address(this).balance == 0` (or track and refund the delta). Alternatively, auto-call `refundETH()` at the end of every `payable` entry-point.

2. **Validate `msg.value` against consumed amount.** For exact-input WETH swaps, require `msg.value == amountIn` or `msg.value == 0` (ERC-20 path). For exact-output, require `msg.value <= amountInMaximum` and refund `msg.value - amountIn` atomically before returning.

3. **Reject non-zero `msg.value` when `tokenIn != WETH`.** If the caller sends ETH but the input token is not WETH, revert immediately to prevent accidental ETH stranding.

---

### Proof of Concept

```
Setup:
  - WETH/Token1 pool exists and is registered in the factory.
  - Victim has 2 ether.
  - Attacker has 0 ETH but has approved the router for WETH (or has 0 WETH).

Step 1 — Victim strands ETH:
  victim calls:
    router.exactOutputSingle{value: 2 ether}(ExactOutputSingleParams{
        pool: wethToken1Pool,
        tokenIn: WETH,
        tokenOut: Token1,
        zeroForOne: true,
        amountOut: 1_000,          // small output
        amountInMaximum: 2 ether,  // victim over-provisions
        recipient: victim,
        ...
    })

  Execution:
    - Pool determines amountIn = 1_001 (example).
    - pay(WETH, victim, pool, 1_001) is called.
    - address(this).balance = 2 ether >= 1_001 → wraps 1_001, sends to pool.
    - Remaining 2 ether - 1_001 ≈ 1.999... ether stays on router.
    - No refund is issued; function returns normally.

Step 2 — Attacker steals stranded ETH:
  attacker calls (msg.value = 0):
    router.exactInputSingle(ExactInputSingleParams{
        pool: wethToken1Pool,
        tokenIn: WETH,
        tokenOut: Token1,
        zeroForOne: true,
        amountIn: 1.999... ether,  // exactly the stranded amount
        amountOutMinimum: 0,
        recipient: attacker,
        ...
    })

  Execution:
    - _setNextCallbackContext(pool, JUST_PAY, attacker, WETH)
    - Pool calls metricOmmSwapCallback → _justPayCallback
    - pay(WETH, attacker, pool, 1.999... ether) is called.
    - address(this).balance = 1.999... ether >= 1.999... ether
      → wraps victim's stranded ETH, sends to pool.
    - Attacker receives Token1 output funded entirely by victim's ETH.

Result:
  - Victim lost ≈ 1.999 ether beyond what the swap required.
  - Attacker received a free swap worth ≈ 1.999 ether.
```

**Affected code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
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
