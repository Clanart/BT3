### Title
Excess native ETH stranded by `pay()` after exact-output swaps is claimable by any caller via `refundETH()` — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

---

### Summary

When a user calls `exactOutputSingle` (or `exactOutput`) with `tokenIn = WETH` and sends `msg.value = amountInMaximum`, the internal `pay()` function wraps only the actual amount the pool requests (`actualAmountIn ≤ amountInMaximum`). The remainder (`msg.value − actualAmountIn`) is left as raw ETH on the router. Because `refundETH()` is a public function that unconditionally transfers the router's entire ETH balance to `msg.sender`, any third party can call it in a subsequent transaction and steal the stranded ETH.

---

### Finding Description

`PeripheryPayments.pay()` handles native-ETH-as-WETH input with this branch:

```solidity
// PeripheryPayments.sol L73-77
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();   // wraps exactly `value`
        IERC20(WETH).safeTransfer(recipient, value);
    }
```

`value` is the amount the pool actually requested in the callback — not `msg.value`. Any ETH above `value` that arrived via `msg.value` is silently left on the router as raw ETH.

`refundETH()` then sends the entire router ETH balance to whoever calls it:

```solidity
// PeripheryPayments.sol L58-63
function refundETH() external payable override {
    uint256 balance = address(this).balance;
    if (balance > 0) {
        _transferETH(msg.sender, balance);   // no attribution, no access control
    }
}
```

There is no per-user accounting, no access control, and no automatic refund at the end of `exactOutputSingle`. The stranded ETH is a free-for-all.

The exact-output entry point is:

```solidity
// MetricOmmSimpleRouter.sol L130-147
function exactOutputSingle(ExactOutputSingleParams calldata params)
    external payable returns (uint256 amountIn)
{
    ...
    _setNextCallbackContext(params.pool, CALLBACK_MODE_JUST_PAY, msg.sender, params.tokenIn);
    (int128 amount0Delta, int128 amount1Delta) = IMetricOmmPoolActions(params.pool)
        .swap(params.recipient, params.zeroForOne, -expectedAmountOut, ...);
    ...
    if (amountIn > params.amountInMaximum) revert InputTooHigh(...);
    _clearExpectedCallbackPool();
    // ← no refundETH(), no automatic return of excess msg.value
}
```

The function is `payable` and standalone — users can call it directly without a multicall wrapper. There is no automatic refund of excess ETH at the end of the function.

---

### Impact Explanation

A user who calls `exactOutputSingle{value: amountInMaximum}(...)` directly (not via a multicall that also encodes `refundETH()`) will have their excess ETH (`amountInMaximum − actualAmountIn`) stranded on the router. Any attacker who observes the transaction (e.g., via mempool monitoring) can immediately call `refundETH()` in the next transaction and receive the full stranded balance. This is a direct, complete loss of the user's excess ETH principal with no recovery path once the attacker claims it.

---

### Likelihood Explanation

Exact-output swaps are designed to accept a user-supplied `amountInMaximum` that is intentionally larger than the expected actual cost (to tolerate price movement). It is therefore routine for `msg.value > actualAmountIn`. Users who call `exactOutputSingle` directly — rather than composing it inside a `multicall([..., refundETH()])` — will strand ETH on every such call. The function is `external payable` with no guard, so the pattern is reachable by any EOA or integrating contract that does not know to wrap the call in a multicall.

---

### Recommendation

Add an automatic refund of any remaining native ETH balance at the end of `exactOutputSingle` and `exactOutput` when `tokenIn == WETH`:

```solidity
function exactOutputSingle(ExactOutputSingleParams calldata params)
    external payable returns (uint256 amountIn)
{
    // ... existing logic ...
    _clearExpectedCallbackPool();

    // Refund any unused native ETH to the caller
    uint256 remaining = address(this).balance;
    if (remaining > 0) {
        _transferETH(msg.sender, remaining);
    }
}
```

Alternatively, document prominently that callers **must** wrap every payable swap call in `multicall([swap(...), refundETH()])` and never call `exactOutputSingle` directly with `msg.value > 0`.

---

### Proof of Concept

```solidity
// Attacker steals Alice's excess ETH from an exactOutputSingle call

// 1. Alice calls exactOutputSingle with amountInMaximum = 1 ETH
//    Actual swap cost = 0.6 ETH → 0.4 ETH stranded on router
router.exactOutputSingle{value: 1 ether}(
    IMetricOmmSimpleRouter.ExactOutputSingleParams({
        pool: address(pool),
        tokenIn: address(weth),
        tokenOut: address(token1),
        zeroForOne: true,
        amountOut: desiredOut,
        amountInMaximum: 1 ether,   // generous cap
        recipient: alice,
        deadline: block.timestamp + 60,
        priceLimitX64: 0,
        extensionData: ""
    })
);
// router.balance == 0.4 ether (stranded)

// 2. Attacker (Bob) calls refundETH() in the next transaction
vm.prank(bob);
router.refundETH();
// Bob receives 0.4 ether — Alice's funds are gone
assertEq(bob.balance, 0.4 ether);
assertEq(address(router).balance, 0);
```

The root cause is the combination of `pay()` wrapping only `value` ETH (leaving `nativeBalance − value` on the router) and `refundETH()` sending the entire balance to an unauthenticated `msg.sender`. [1](#0-0) [2](#0-1) [3](#0-2)

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
