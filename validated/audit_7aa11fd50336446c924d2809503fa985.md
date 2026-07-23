Audit Report

## Title
Router `pay()` consumes stranded native ETH from prior transactions to settle subsequent WETH swaps, causing permanent fund loss for the original ETH depositor — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` resolves WETH payment obligations by inspecting `address(this).balance` — the router's total native ETH balance — rather than the ETH contributed by the current caller via `msg.value`. When a user sends excess ETH in a WETH swap and omits a trailing `refundETH()` call, the surplus is stranded on the router. Any subsequent WETH swap by any address will silently consume that stranded ETH to satisfy its own payment obligation, permanently destroying the original depositor's funds while the second user pays nothing from their own balance.

## Finding Description
In `PeripheryPayments.sol` lines 73–84, the WETH branch of `pay()` reads `uint256 nativeBalance = address(this).balance` and, when `nativeBalance >= value`, wraps exactly `value` ETH and transfers it to the recipient without pulling any WETH from `payer`:

```solidity
} else if (token == WETH) {
    uint256 nativeBalance = address(this).balance;   // total router ETH
    if (nativeBalance >= value) {
        IWETH9(WETH).deposit{value: value}();
        IERC20(WETH).safeTransfer(recipient, value);
    } ...
}
```

The `payer` argument (the actual swap initiator stored in transient context) is completely bypassed in this branch. Because `nativeBalance` is the router's aggregate balance — including ETH left over from any prior transaction — a second caller whose `msg.value` is zero can have their entire WETH obligation satisfied by a previous user's stranded ETH.

The `receive()` guard at line 32–34 only blocks direct ETH sends from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` in payable entry points (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `refundETH`, `multicall`). All four swap functions are `payable` and set `payer = msg.sender` for the first hop via `_setNextCallbackContext`, but none enforce that `msg.value` equals `amountIn` or zero.

`refundETH()` at lines 58–63 is a separate, optional call that refunds the entire router balance to `msg.sender`. It is not automatically appended by any swap function, and if a second user's swap executes before the original depositor calls it, the stranded ETH is irrecoverably consumed.

## Impact Explanation
Direct, irreversible loss of user principal (native ETH). User A's excess ETH is wrapped and forwarded to a pool on behalf of User B, with no on-chain recovery path. User A's subsequent `refundETH()` call returns zero. This satisfies the "Critical/High direct loss of user principal" impact gate: the exact corrupted value is the stranded ETH balance on the router, consumed by an unprivileged third-party swap.

## Likelihood Explanation
Sending slightly more ETH than the exact swap amount is the standard defensive pattern for native-ETH swaps to avoid reverts from slippage. The `multicall` + `refundETH()` idiom is not enforced by any swap function. Any user who calls `exactInputSingle` (or the other swap entry points) directly with excess `msg.value` — without a trailing `refundETH()` — is immediately vulnerable. The trigger is any other user's WETH swap in a subsequent block, which is a routine, zero-cost, unprivileged action.

## Recommendation
Track the ETH contributed by the current call separately from any pre-existing router balance. Concretely: record `msg.value` at the top of each payable entry point and pass it explicitly into `pay()` as the maximum native ETH available for this call. Alternatively, enforce that `msg.value == 0` unless `tokenIn == WETH`, and if `tokenIn == WETH` require `msg.value == amountIn` exactly, reverting otherwise. At minimum, add an internal assertion that the router's ETH balance after payment equals the pre-call balance minus the amount used, and document that callers must append `refundETH()` in a multicall.

## Proof of Concept
```
Block N:
  User A calls exactInputSingle{value: 2 ETH}(
      tokenIn=WETH, amountIn=1 ETH, recipient=A, ...
  )
  → _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, payer=A, token=WETH)
  → pool.swap() triggers metricOmmSwapCallback → _justPayCallback
  → pay(WETH, A, pool, 1 ETH)
      nativeBalance = 2 ETH >= 1 ETH
      deposit(1 ETH) → safeTransfer 1 WETH to pool   ✓
      1 ETH remains on router
  → A does NOT call refundETH()

Block N+1:
  User B calls exactInputSingle{value: 0}(
      tokenIn=WETH, amountIn=1 ETH, recipient=B, ...
  )
  → _setNextCallbackContext(pool, CALLBACK_MODE_JUST_PAY, payer=B, token=WETH)
  → pool.swap() triggers metricOmmSwapCallback → _justPayCallback
  → pay(WETH, B, pool, 1 ETH)
      nativeBalance = 1 ETH (A's stranded ETH) >= 1 ETH
      deposit(1 ETH) → safeTransfer 1 WETH to pool   ← A's ETH pays B's swap
      payer B is never charged
  → B receives swap output for free

User A calls refundETH():
  address(this).balance == 0  →  transfers 0 ETH to A
  A has permanently lost 1 ETH
```

A Foundry test can reproduce this by deploying the router with a mock WETH and mock pool, having address A call `exactInputSingle{value: 2 ether}` with `amountIn=1 ether`, then having address B call `exactInputSingle{value: 0}` with `amountIn=1 ether`, and asserting that A's subsequent `refundETH()` returns 0 ETH and B's swap succeeded without any WETH transfer from B.