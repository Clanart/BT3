Audit Report

## Title
Pre-existing ETH Balance in Router Used to Pay Attacker's WETH Swap for Free — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` function reads `address(this).balance` to determine how much native ETH to contribute toward a WETH payment, rather than tracking only the current call's `msg.value`. Any ETH left in the router from a prior user who did not call `refundETH()` can be silently consumed to fund a subsequent caller's WETH swap, with no `safeTransferFrom` ever called on that caller. The victim permanently loses their stranded ETH; the attacker receives output tokens at zero cost.

## Finding Description
In `PeripheryPayments.sol` L74–84, `pay()` branches on `address(this).balance`:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);   // payer pays nothing
} else if (nativeBalance > 0) { ... }
else { IERC20(WETH).safeTransferFrom(payer, recipient, value); }
```

When `nativeBalance >= value`, the function wraps contract-held ETH and transfers WETH to the pool without ever pulling tokens from `payer`. ETH accumulates in the router whenever a user calls a `payable` entry point (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) with `msg.value` exceeding the actual swap amount and omits a trailing `refundETH()` call. The `receive()` guard at L32–34 only blocks arbitrary external ETH pushes (non-WETH senders), but does not prevent ETH from accumulating via `msg.value` in payable swap functions. `refundETH()` at L57–63 is opt-in and never called automatically.

The exploit path:
1. Victim calls `exactInputSingle(tokenIn=WETH, amountIn=X)` with `msg.value > X`, does not call `refundETH()`. Router retains `msg.value - X` ETH.
2. Attacker calls `exactInputSingle(tokenIn=WETH, amountIn=X, msg.value=0)`.
3. `_setNextCallbackContext` records attacker as `payer` (L71).
4. Pool calls `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, attacker, pool, X)` (L192–198).
5. `nativeBalance = address(this).balance >= X` → branch taken: router wraps X ETH, transfers WETH to pool. No `safeTransferFrom` on attacker.
6. Attacker receives output tokens; victim's ETH is gone.

## Impact Explanation
Direct loss of user principal: a victim's stranded ETH is permanently consumed to fund an attacker's trade. The attacker receives full swap output at zero cost. This is a Critical/High direct loss of user funds meeting Sherlock thresholds — the exact corrupted value is `address(this).balance` being consumed from a prior depositor rather than from the current `payer`.

## Likelihood Explanation
Overpaying `msg.value` and omitting `refundETH()` is a well-documented common mistake in Uniswap-style routers. The router's ETH balance is trivially readable on-chain. The attacker needs no special permissions, no malicious setup, and no privileged role — only the ability to observe a non-zero router ETH balance and submit a WETH-input swap with `msg.value = 0`. The `multicall` function (L39–44) is `payable` and uses `delegatecall`, making it easy to batch a WETH swap without a refund step, further increasing the likelihood of stranded ETH.

## Recommendation
Replace the `address(this).balance` read in `pay()` with a parameter representing only the ETH contributed by the current transaction. Pass `msg.value` (or a remaining-ETH counter decremented as ETH is consumed) down from each entry-point function through to `pay()`. This ensures leftover ETH from prior transactions is never silently applied to a new payer's obligation. Alternatively, enforce that any ETH-to-WETH conversion in `pay()` only uses ETH up to the amount explicitly passed in by the current caller.

## Proof of Concept
1. **Victim**: Call `exactInputSingle({tokenIn: WETH, amountIn: 0.5 ether, ...})` with `msg.value = 1 ether`. Swap executes; `pay()` reads `nativeBalance = 1 ether >= 0.5 ether`, wraps 0.5 ETH, transfers WETH to pool. Victim receives output tokens. Victim does **not** call `refundETH()`. Router now holds **0.5 ETH**.
2. **Attacker**: Call `exactInputSingle({tokenIn: WETH, amountIn: 0.5 ether, msg.value: 0, ...})`.
   - Pool triggers `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, attacker, pool, 0.5 ether)`.
   - `nativeBalance = address(this).balance = 0.5 ether >= 0.5 ether` → branch taken.
   - Router wraps 0.5 ETH, transfers WETH to pool. No `safeTransferFrom` on attacker.
   - Attacker receives output tokens worth 0.5 ETH at zero cost.
3. **Result**: Victim loses 0.5 ETH permanently; attacker profits by 0.5 ETH worth of output tokens.

A Foundry test can reproduce this by: deploying the router with a mock WETH and pool, simulating the victim's overpayment, then calling `exactInputSingle` from a fresh attacker address with `msg.value = 0` and asserting that no WETH is pulled from the attacker while the attacker receives output tokens.