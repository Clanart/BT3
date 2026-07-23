The `receive()` guard at line 32-34 only blocks plain ETH transfers (no calldata). ETH sent as `msg.value` with payable function calls like `exactOutputSingle` bypasses it entirely and accumulates in the router. Let me verify there are no other guards.

The `grep_search` for `msg.value` returns **no matches** across all periphery contracts — confirming there is zero per-call ETH budget tracking anywhere in the router. Combined with the confirmed code in `PeripheryPayments.sol`, the vulnerability is real.

Key facts:
- `receive()` [1](#0-0)  only blocks plain ETH transfers (no calldata). ETH sent as `msg.value` with payable calls like `exactOutputSingle` bypasses it and accumulates in the router.
- `pay()` uses `address(this).balance` — the router's **total** ETH — not the current call's `msg.value`. [2](#0-1) 
- `msg.value` is never referenced anywhere in the periphery contracts, so there is no per-call ETH budget.
- `exactOutputSingle` sets `payer = msg.sender` and `tokenIn` in transient storage, then the callback calls `pay(WETH, payer, pool, amountIn)` — if `address(this).balance >= amountIn`, no `transferFrom` on the payer occurs. [3](#0-2) 

---

Audit Report

## Title
Cross-User ETH Theft via Residual Router Balance in WETH Payment Path — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay` settles WETH-denominated swap payments using `address(this).balance` — the router's total ETH — rather than the current caller's `msg.value`. Because `msg.value` is never tracked anywhere in the router, ETH left over from a prior user's overpayment can be silently consumed to fully settle a subsequent user's swap, with no funds pulled from that subsequent user.

## Finding Description
`exactOutputSingle` (and `exactInput`/`exactOutput`) are `payable` and store `payer = msg.sender` in transient storage via `_setNextCallbackContext`. When the pool fires `metricOmmSwapCallback`, `_justPayCallback` calls `pay(WETH, payer, pool, amountIn)`.

Inside `pay`:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // router's TOTAL ETH
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value); // payer never pulled
    } ...
}
```

The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) only blocks plain ETH transfers with no calldata. ETH sent as `msg.value` with any payable function call accumulates freely. `msg.value` is never referenced anywhere in the periphery contracts (confirmed by exhaustive search), so there is no per-call budget.

Attack path:
1. User A calls `exactOutputSingle{value: X}(tokenIn=WETH, amountInMaximum=X)`. Actual `amountIn_A < X`. Router retains `X − amountIn_A` ETH (User A did not call `refundETH()`).
2. User B calls `exactOutputSingle{value: 0}(tokenIn=WETH, amountInMaximum=type(uint256).max)`.
3. `pay(WETH, UserB, pool, amountIn_B)` fires. `nativeBalance = X − amountIn_A`. If `nativeBalance >= amountIn_B`, the router wraps User A's ETH and transfers it to the pool. No `safeTransferFrom` on User B occurs.
4. User B receives full swap output at zero cost. User A's ETH is permanently consumed.

Existing guards are insufficient: `amountInMaximum` at line 145 only checks `amountIn <= amountInMaximum`, not that the caller contributed any funds. The callback caller check (`_requireExpectedCallbackCaller`) only validates the pool identity, not payment source.

## Impact Explanation
Direct loss of User A's ETH principal. User B receives a fully-settled swap without contributing any funds. Loss is bounded by the residual ETH in the router at attack time, which can equal the full `msg.value` of a prior transaction. This is a direct loss of user principal — High severity under Sherlock thresholds.

## Likelihood Explanation
ETH stranding is a routine occurrence: any user who calls `exactOutputSingle{value: X}` with `tokenIn=WETH` where `X > actualAmountIn` and does not bundle `refundETH()` in a `multicall` leaves residual ETH. The attack requires no special permissions, no malicious pool, no non-standard tokens, and no privileged role — only a public call to `exactOutputSingle` with `msg.value=0`.

## Recommendation
Track the ETH available to the current call rather than the router's total balance. The standard approach is to pass `msg.value` as a parameter through the call stack and consume only from it, or store it in transient storage at the top-level entry point and decrement it in `pay`. Alternatively, enforce at each top-level entry that `address(this).balance == msg.value` (revert if residual ETH exists), though this is more restrictive and breaks composability.

## Proof of Concept
```solidity
function test_crossUserETHTheft() public {
    // User A: swap with excess ETH, no refundETH
    router.exactOutputSingle{value: 1 ether}(ExactOutputSingleParams({
        pool: pool, tokenIn: WETH, tokenOut: tokenB,
        amountOut: smallAmount, amountInMaximum: 1 ether,
        recipient: userA, zeroForOne: true, ...
    }));
    uint256 residual = address(router).balance; // > 0

    // User B: zero msg.value, steals router's ETH
    router.exactOutputSingle{value: 0}(ExactOutputSingleParams({
        pool: pool, tokenIn: WETH, tokenOut: tokenB,
        amountOut: smallAmount, amountInMaximum: type(uint256).max,
        recipient: userB, zeroForOne: true, ...
    }));

    // Router's ETH was consumed; User B paid nothing
    assertLt(address(router).balance, residual);
    assertEq(IERC20(tokenB).balanceOf(userB), smallAmount);
}
```

### Citations

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L32-34)
```text
  receive() external payable {
    if (msg.sender != WETH) revert NotWETH();
  }
```

**File:** metric-periphery/contracts/base/PeripheryPayments.sol (L74-77)
```text
      uint256 nativeBalance = address(this).balance;
      if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
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
