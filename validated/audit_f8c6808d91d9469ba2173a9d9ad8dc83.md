Audit Report

## Title
Stale Router ETH Balance Consumed by Subsequent User's WETH Payment — (File: metric-periphery/contracts/base/PeripheryPayments.sol)

## Summary
The `pay()` function in `PeripheryPayments` reads `address(this).balance` unconditionally when settling a WETH obligation, with no check that the native ETH belongs to the current transaction. ETH stranded on the router by a prior user (who sent excess `msg.value` without calling `refundETH()`) is silently consumed to satisfy a subsequent user's WETH swap, causing the original depositor to lose their funds permanently.

## Finding Description
`exactInputSingle` is `payable`, so callers may send ETH with the call. When `tokenIn == WETH`, the call stack is: `exactInputSingle` → pool `swap` → `metricOmmSwapCallback` → `_justPayCallback` → `pay(WETH, payer, pool, value)`.

Inside `pay()` at lines 73–84 of `PeripheryPayments.sol`:

```solidity
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

The function reads the router's **entire** native ETH balance and uses it to cover the current user's WETH obligation before pulling from `payer`'s wallet. There is no attribution of which ETH belongs to which transaction.

ETH is stranded when a user calls `exactInputSingle{value: X}(amountIn: Y, tokenIn: WETH, ...)` where `X > Y` and omits `refundETH()`. The `receive()` guard (`if (msg.sender != WETH) revert NotWETH()`) does **not** prevent this: ETH sent with a `payable` function call bypasses `receive()` entirely — it is only triggered on bare ETH transfers with no calldata. The surplus `X - Y` ETH remains on the router across transaction boundaries.

In a subsequent transaction, any user calling a WETH-input swap triggers `pay(WETH, newUser, pool, amount)`. If `address(this).balance >= amount`, the stranded ETH is deposited as WETH and forwarded to the pool, fully satisfying the new user's obligation without touching their wallet. The transient payer context correctly identifies the new user as `payer`, but `pay()` ignores that identity when native ETH is available.

Existing guards are insufficient: `_requireExpectedCallbackCaller` only validates the pool address; `_getPayer()` correctly returns the new user but `pay()` does not use that identity to gate ETH consumption.

## Impact Explanation
Direct loss of user principal: User A's stranded ETH is permanently consumed by User B's swap. User A receives no compensation and has no recovery path. User B receives the full swap output without spending any WETH from their wallet. The pool receives the correct WETH amount so pool solvency is unaffected, but the router acts as an unattributed ETH sink that redistributes value between users. Loss magnitude equals the stranded ETH amount, which is unbounded (any excess `msg.value` without `refundETH()`). This meets the Critical/High threshold for direct loss of user principal.

## Likelihood Explanation
`exactInputSingle` is a public `payable` function callable by any user or integrator. Sending excess ETH without `refundETH()` is a natural mistake: the function is callable directly (not only via multicall), and wallets/integrators may send ETH without constructing a multicall bundle. Any subsequent WETH-input swap by any user in any later block drains the stranded balance. No special permissions, privileged access, or coordination are required.

## Recommendation
Track only the ETH that arrived in the current transaction as eligible for WETH conversion. The standard approach is to record `msg.value` at the top-level entry point (e.g., in `multicall` or each payable swap function) and pass it explicitly through the call stack, decrementing it as it is consumed in `pay()`. Alternatively, compare `address(this).balance` before and after the call to isolate the current transaction's contribution. This ensures stranded ETH from prior transactions is never silently consumed.

## Proof of Concept
```
Setup:
  - Router deployed with WETH address.
  - Pool: WETH (token0) / ERC20 (token1).
  - User A: 2000 wei ETH.
  - User B: 0 ETH, 0 WETH; wants to swap 1000 WETH → token1.

Step 1 (User A, Tx 1):
  User A calls exactInputSingle{value: 2000}(amountIn=1000, tokenIn=WETH, ...)
  → pay() sees nativeBalance=2000 >= 1000, deposits 1000 ETH as WETH, pays pool.
  → 1000 ETH remains on router (User A omits refundETH()).

Step 2 (User B, Tx 2):
  User B calls exactInputSingle{value: 0}(amountIn=1000, tokenIn=WETH, ...)
  → pay(WETH, UserB, pool, 1000) is called.
  → nativeBalance = 1000 (User A's stranded ETH).
  → nativeBalance >= value: deposits 1000 ETH as WETH, transfers to pool.
  → User B's WETH balance: unchanged (0 spent).
  → User B receives token1 output.

Result:
  User A: lost 1000 ETH permanently.
  User B: received token1 output at zero cost.
  Pool: received correct WETH, unaware of misattribution.
```

Foundry test: deploy router + mock WETH + mock pool; execute Step 1 then Step 2 from different addresses; assert `address(router).balance == 0` after Step 2 and User A's ETH is unrecoverable.