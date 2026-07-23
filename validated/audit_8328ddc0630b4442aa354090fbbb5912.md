Audit Report

## Title
Router `pay()` WETH Branch Silently Consumes Stranded Native ETH, Enabling Free Swaps at Prior Users' Expense — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` helper in `PeripheryPayments.sol` uses `address(this).balance` unconditionally when paying WETH, with no per-call accounting of who deposited that ETH. Any native ETH left on the router from a prior transaction (excess `msg.value` not reclaimed via `refundETH`) is freely consumed by the next caller's WETH-input swap, giving that caller a full swap output at zero cost while the prior user permanently loses their ETH.

## Finding Description
`PeripheryPayments.pay()` (L73–84) handles WETH payments by reading the router's raw ETH balance:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);   // payer never touched
}
```

When `nativeBalance >= value`, the `payer` argument is completely ignored. There is no transient-storage slot or any other mechanism that ties the router's ETH balance to the specific top-level call that deposited it.

The call path is: `exactInputSingle` (L67, `payable`, stores `msg.sender` as payer via `_setNextCallbackContext`) → pool `swap()` → `metricOmmSwapCallback` (L46) → `_justPayCallback` (L192) → `pay()`. Because `exactInputSingle` is `payable` and imposes no constraint that `msg.value == params.amountIn`, a user can send excess ETH. After the callback consumes only `amountIn` worth of ETH, the remainder sits on the router. The `receive()` guard (L32–34) only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` across separate transactions.

In the next block, any caller invoking `exactInputSingle{value: 0}` with `tokenIn = WETH` and `amountIn ≤ stranded balance` will have `pay()` wrap and forward the stranded ETH on their behalf, receiving full swap output without spending any ETH or WETH of their own.

Existing guards are insufficient: `_requireExpectedCallbackCaller` (L49) only validates the pool address, not the ETH source; `refundETH` (L58–63) is opt-in and not enforced at entry or exit of any swap function.

## Impact Explanation
Direct loss of user principal. The victim's stranded ETH is permanently transferred to the pool as payment for an attacker's trade. The attacker receives the full token output at zero cost. This meets the Sherlock "direct loss of user principal" threshold at Medium–High severity, depending on the frequency of stranding (which is non-negligible given common `multicall` patterns that omit `refundETH`).

## Likelihood Explanation
ETH stranding arises naturally when a user sends `msg.value > amountIn` (e.g., to avoid partial-fill reverts) or uses `multicall` without a trailing `refundETH` call. Both patterns are common in production router usage. Once ETH is stranded, any observer can exploit it in the very next block with a zero-cost WETH swap requiring no special privileges or setup.

## Recommendation
**Short term:** In the `nativeBalance >= value` branch, verify that the ETH credit for the current call covers `value`. Store the per-call ETH credit in transient storage at the start of each payable entry point (alongside the existing payer/token context) and deduct from it rather than from the raw contract balance.

**Long term:** Enforce that `address(this).balance == 0` at the start of every non-payable entry point, or revert in `pay()` when `nativeBalance > 0` and `msg.value` for the current top-level call is zero.

## Proof of Concept
```
Block N:
  Alice calls exactInputSingle{value: 1000}(tokenIn=WETH, amountIn=500, ...)
  → metricOmmSwapCallback → _justPayCallback → pay(WETH, Alice, pool, 500)
      nativeBalance = 1000 >= 500
      router wraps 500 wei, sends WETH to pool  ← 500 wei of Alice's ETH used
      500 wei remains on router; Alice omits refundETH()

Block N+1:
  Bob calls exactInputSingle{value: 0}(tokenIn=WETH, amountIn=500, ...)
  → metricOmmSwapCallback → _justPayCallback → pay(WETH, Bob, pool, 500)
      nativeBalance = 500 >= 500
      router wraps 500 wei (Alice's), sends WETH to pool
      Bob receives TOKEN output, pays nothing
```

Alice loses 500 wei permanently. Bob receives a full swap output at zero cost. A Foundry fork test can reproduce this by: (1) funding Alice's call with excess ETH, (2) skipping `refundETH`, (3) having Bob call with `value: 0` and asserting his token balance increases while his ETH balance is unchanged.