Audit Report

## Title
`address(this).balance` Used Instead of Per-Transaction Tracked ETH in `pay()`, Allowing Stranded Native ETH to Subsidize Subsequent Users' WETH Obligations — (File: `metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary

`PeripheryPayments.pay()` reads `address(this).balance` to determine how much native ETH to wrap as WETH when settling a swap or liquidity add. Because this is the contract's aggregate balance — not a per-transaction counter — any ETH left on the contract by a prior user who omitted `refundETH` is silently consumed to cover a later user's WETH obligation. The prior user loses the stranded ETH; the later user's `safeTransferFrom` pull is reduced or eliminated entirely.

## Finding Description

In `PeripheryPayments.pay()` (L74), the WETH branch reads the full contract balance:

```solidity
uint256 nativeBalance = address(this).balance;   // aggregate, not per-tx
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

The `receive()` guard at L32–34 only blocks direct ETH pushes from non-WETH addresses. It does not prevent ETH from accumulating across transactions via the `payable` entry points `exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, and `multicall` on `MetricOmmSimpleRouter`, and `addLiquidityExactShares`, `addLiquidityWeighted`, and `multicall` on `MetricOmmPoolLiquidityAdder`.

There is no per-transaction ETH tracking. The transient storage context in `MetricOmmSwapRouterBase` / `TransientCallbackPool` records pool, callbackMode, payer, tokenToPay, tradesLeft, and amountIn — but not `msg.value` or any remaining-ETH counter. `refundETH()` exists but is an opt-in external call; it is not invoked automatically at the end of any swap or liquidity function.

Exploit path:
1. User A calls `exactInputSingle{value: 1 ETH}` with `tokenIn = WETH`, `amountIn = 0.5 ETH`, omitting `refundETH`. The callback fires `pay(WETH, userA, pool, 0.5 ETH)`; `address(this).balance = 1 ETH ≥ 0.5 ETH`, so 0.5 ETH is wrapped and forwarded. The remaining 0.5 ETH stays on the router.
2. User B calls `exactInputSingle` with zero `msg.value`, `tokenIn = WETH`, `amountIn = 0.3 ETH`, and zero WETH allowance. The callback fires `pay(WETH, userB, pool, 0.3 ETH)`; `address(this).balance = 0.5 ETH ≥ 0.3 ETH`, so User A's stranded ETH is wrapped and forwarded. `safeTransferFrom` on User B is never called. User B's swap succeeds at zero cost.

## Impact Explanation

User A suffers a direct loss of principal beyond their intended spend (0.3 ETH in the example). User B receives a fully subsidized swap. The pool receives the correct WETH amount in both cases, so pool solvency is unaffected; the loss is entirely borne by the user whose ETH was stranded. This constitutes a direct loss of user principal reachable by any unprivileged caller, meeting the High severity threshold under Sherlock contest rules.

## Likelihood Explanation

Users routinely send `msg.value` with WETH-input swaps to avoid a separate wrap step and frequently omit `refundETH` when the exact consumed amount is not known in advance. The trigger is fully unprivileged: any address can call `exactInputSingle` or `addLiquidityExactShares` with zero `msg.value` and WETH as the input token to drain whatever ETH is currently stranded on the contract. No oracle manipulation, special timing, or privileged access is required; the attacker only needs to monitor the router's ETH balance.

## Recommendation

Track the ETH allocated to the current transaction in a dedicated transient storage slot (consistent with the existing transient callback-context pattern in `TransientCallbackPool`). Record `msg.value` at entry to each `payable` function and decrement it as ETH is consumed in `pay()`. In `pay()`, read only the per-transaction remaining ETH rather than `address(this).balance`:

```solidity
uint256 nativeBalance = _tload(T_SLOT_MSG_VALUE_REMAINING);
```

Alternatively, automatically call `refundETH` at the end of every `payable` entry point, but this is a weaker mitigation because it relies on correct implementation across all current and future entry points.

## Proof of Concept

```solidity
// 1. User A strands 0.5 ETH on the router
router.exactInputSingle{value: 1 ether}(ExactInputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 0.5 ether,
    amountOutMinimum: 0,
    recipient: userA,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// router.balance == 0.5 ETH; no refundETH called

// 2. User B drains User A's ETH — zero msg.value, zero WETH allowance
router.exactInputSingle(ExactInputSingleParams({
    pool: pool,
    tokenIn: address(weth),
    tokenOut: address(token1),
    zeroForOne: true,
    amountIn: 0.3 ether,
    amountOutMinimum: 0,
    recipient: userB,
    deadline: block.timestamp + 1,
    priceLimitX64: 0,
    extensionData: ""
}));
// User B's swap succeeds; 0.3 ETH of User A's residue consumed
// assertEq(address(router).balance, 0.2 ether);
// assertEq(userA net ETH loss, 0.3 ether beyond intended spend);
```