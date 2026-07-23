Audit Report

## Title
Router `pay()` Consumes Unattributed Native ETH Balance, Enabling Cross-Transaction Theft of Stranded ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` uses `address(this).balance` — the router's total native ETH balance — without restricting consumption to the current transaction's `msg.value`. Any ETH stranded on the router from a prior transaction is silently consumed to pay a subsequent caller's swap, or can be directly stolen via the public `refundETH()` helper. This results in a complete, irreversible loss of the stranded user's ETH principal.

## Finding Description

In `pay()` (lines 69–88 of `PeripheryPayments.sol`), when `token == WETH` and `payer != address(this)`, the function reads `address(this).balance` and uses it to fund the WETH deposit before falling back to `safeTransferFrom(payer, ...)`:

```solidity
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

`address(this).balance` is not scoped to the current transaction's `msg.value`. ETH stranded from a prior transaction is indistinguishable from the current caller's ETH and is consumed first.

The `receive()` guard (lines 32–34) only blocks plain ETH pushes from non-WETH addresses. It does **not** prevent ETH from accumulating via `msg.value` in the payable swap functions (`exactInputSingle`, `exactOutputSingle`, `exactInput`, `exactOutput`, `multicall`), all of which are declared `payable` and accept ETH directly without triggering `receive()`.

`refundETH()` (lines 58–63) compounds this: it is public, takes no arguments, and sends the router's **entire** ETH balance to `msg.sender` with no check that the caller deposited any of that ETH.

`TransientCallbackPool` correctly binds the expected pool, payer, and token per-swap in transient storage, but it does not track how much native ETH the current payer contributed. `pay()` therefore has no mechanism to limit itself to the current caller's ETH.

**Exploit path:**

1. Alice calls `exactOutputSingle{value: 2000}(...)` with WETH as `tokenIn` and `amountOut = 1000`. The pool determines `amountIn = 1500`. `pay()` deposits 1500 ETH → WETH → pool. 500 ETH remains on the router. Alice did not include `refundETH()` in the same multicall.
2. Bob calls `refundETH()` in the next block. Bob receives 500 ETH. Alice's ETH is gone.

Alternatively, Bob calls `exactInputSingle{value: 0}(...)` with `amountIn = 500` and WETH as `tokenIn`. `pay()` sees `address(this).balance = 500 >= 500`, deposits Alice's 500 ETH → WETH → pool, and Bob receives token output having paid nothing.

## Impact Explanation

Direct loss of user principal. Alice's stranded ETH is either consumed by Bob's swap (Bob receives output tokens without paying any input) or directly stolen via `refundETH()`. Both paths result in a complete, irreversible loss of Alice's ETH with no protocol recourse. This meets the Critical/High threshold for direct loss of user principal under the contest's allowed impact gate.

## Likelihood Explanation

The trigger is realistic and common. Exact-output WETH swaps (`exactOutputSingle`, `exactOutput`) require the user to send `amountInMaximum` as `msg.value` because the actual `amountIn` is determined by the pool at execution time. The difference `amountInMaximum - amountIn` is stranded unless `refundETH()` is included in the same multicall. Any integration calling `exactOutputSingle{value: amountInMaximum}(...)` directly (not via multicall) will strand ETH on every successful swap. Attacker cost is zero: calling `refundETH()` costs only gas, and the attacker can monitor the router's ETH balance on-chain or via mempool observation.

## Recommendation

Track the native ETH contributed by the current transaction in a transient storage slot at the entry of each payable swap function. In `pay()`, limit the native ETH consumed to `min(address(this).balance, trackedMsgValue)` and decrement the tracker after use. Alternatively, store `msg.value` in a transient slot at function entry and zero it after `pay()` consumes it, ensuring ETH from prior transactions is never accessible to subsequent callers. This mirrors the fix pattern for the analogous Uniswap v3 periphery issue.

## Proof of Concept

**Setup:** Pool with WETH/token1. Alice and Bob are separate EOAs.

**Step 1 — Alice strands ETH (realistic exact-output scenario):**
```solidity
// Alice sends amountInMaximum = 2000 as msg.value for an exact-output swap.
router.exactOutputSingle{value: 2000}(ExactOutputSingleParams({
    pool: pool, tokenIn: WETH, tokenOut: token1,
    zeroForOne: true, amountOut: 1000, amountInMaximum: 2000,
    recipient: alice, deadline: block.timestamp + 1,
    priceLimitX64: 0, extensionData: ""
}));
// Pool determines amountIn = 1500. pay() deposits 1500 ETH → WETH → pool.
// 500 ETH remains on the router. Alice did not include refundETH().
```

**Step 2 — Bob steals Alice's stranded ETH:**
```solidity
// Bob calls refundETH() in the next block.
router.refundETH(); // Bob receives 500 ETH. Alice's ETH is gone.
```

**Alternative Step 2 — Bob gets a free swap:**
```solidity
// Bob calls exactInputSingle with msg.value = 0.
router.exactInputSingle{value: 0}(ExactInputSingleParams({
    pool: pool, tokenIn: WETH, tokenOut: token1,
    zeroForOne: true, amountIn: 500, amountOutMinimum: 0,
    recipient: bob, deadline: block.timestamp + 1,
    priceLimitX64: 0, extensionData: ""
}));
// pay() sees address(this).balance = 500 >= 500.
// Deposits Alice's 500 ETH → WETH → pool. Bob receives token1. Bob paid nothing.
```