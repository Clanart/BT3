### Title
Stranded native ETH on router is silently consumed by any subsequent WETH swap — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

The `pay` helper in `PeripheryPayments` uses `address(this).balance` — the router's **total** native ETH balance — when settling a WETH leg, with no attribution to the current caller's `msg.value`. When ETH is left on the router after a partial-fill `exactInputSingle` (price-limit hit) or after a multicall that omits `refundETH`, any subsequent WETH swap by any caller silently consumes that stranded ETH, causing the original depositor to lose principal.

---

### Finding Description

`pay` in `PeripheryPayments.sol` handles the WETH branch as follows: [1](#0-0) 

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // ← entire router balance
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

`address(this).balance` is the **aggregate** ETH on the router, not the ETH contributed by the current call. Any ETH left over from a prior transaction is indistinguishable from the current caller's `msg.value` and will be spent first.

**How ETH becomes stranded — path 1 (partial fill):**

`exactInputSingle` accepts a `priceLimitX64` and passes it directly to the pool: [2](#0-1) 

When the price limit is hit, the pool executes a partial fill and calls `metricOmmSwapCallback` with the **actual** deltas, which are smaller than `params.amountIn`. The callback pays only the actual amount: [3](#0-2) 

`exactInputSingle` has **no check** that `amountInActual == params.amountIn` (contrast with `exactInput`, which reverts at line 115 if `amountInActual < amount`): [4](#0-3) 

The transaction succeeds, the excess ETH (`params.amountIn − actualAmountIn`) stays on the router, and no revert returns it to the caller.

**How ETH becomes stranded — path 2 (excess `msg.value`):**

The standard multicall-with-ETH pattern sends excess ETH and relies on `refundETH` in the same batch. If a user omits `refundETH`, the surplus is permanently stranded until another caller's WETH swap consumes it. [5](#0-4) 

`refundETH` sends to `msg.sender`, so it cannot be called by the victim after the fact without a race condition.

---

### Impact Explanation

**Direct loss of user principal.** The victim's stranded ETH is deposited as WETH and transferred to a pool on behalf of an attacker's swap. The attacker receives the full swap output without spending any of their own ETH. The victim receives nothing in return for the consumed ETH. This satisfies the "Critical/High/Medium direct loss of user principal" gate.

---

### Likelihood Explanation

**Medium.** Two realistic triggers exist:

1. Any user who calls `exactInputSingle{value: X}` with a non-trivial `priceLimitX64` and receives a partial fill leaves `X − actual` ETH on the router. The pool's oracle-anchored bin model makes partial fills routine when the price limit is tighter than the current spread.
2. Any user who follows the standard multicall-with-ETH pattern but omits `refundETH` (a common mistake documented in Uniswap v3 audits) leaves excess ETH on the router.

The attacker requires no special privilege: a single `exactInputSingle` call with `tokenIn = WETH` and `amountIn ≤ router.balance` suffices.

---

### Recommendation

**Short term:** Snapshot `address(this).balance` before each payable entry point and cap native ETH usage in `pay` to that snapshot minus any ETH already spent in the current call sequence. Alternatively, track a `_nativeDeposited` transient variable that is incremented by `msg.value` at entry and decremented as ETH is consumed, and use that as the cap.

**Long term:** Enforce that `exactInputSingle` reverts when the actual input amount is less than `params.amountIn` (matching the guard already present in `exactInput`), so partial fills always revert and return ETH to the caller rather than leaving it stranded.

---

### Proof of Concept

```
Setup:
  - Pool: WETH / token1, oracle bid/ask set so that a price limit of P
    causes a partial fill of 60 % of any given amountIn.

Step 1 — Victim strands ETH:
  Alice calls:
    router.exactInputSingle{value: 1000}(
      pool, WETH, token1, zeroForOne=true,
      amountIn=1000, amountOutMinimum=0,
      priceLimitX64=P,   // causes 40 % partial fill
      ...
    )
  Pool executes 600 units, callback pays 600 from address(this).balance.
  Transaction succeeds. Router now holds 400 wei of ETH.
  Alice has no on-chain mechanism to reclaim it atomically.

Step 2 — Attacker steals:
  Bob calls (no msg.value):
    router.exactInputSingle(
      pool, WETH, token1, zeroForOne=true,
      amountIn=400, amountOutMinimum=0,
      priceLimitX64=0,   // open limit, full fill
      ...
    )
  pay() sees address(this).balance = 400 >= 400.
  Deposits Alice's 400 wei as WETH, transfers to pool.
  Bob receives token1 output. Alice's 400 wei is gone.

Result:
  Alice loses 400 wei of ETH (40 % of her deposit).
  Bob gains a full swap at zero cost.
```

Key code references:
- `pay` WETH branch: [1](#0-0) 
- Missing input-consumed check in `exactInputSingle` (present in `exactInput` at line 115 but absent here): [2](#0-1) 
- `_justPayCallback` pays actual delta, not `params.amountIn`: [3](#0-2)

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
