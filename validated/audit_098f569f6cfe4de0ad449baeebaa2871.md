Audit Report

## Title
Unattributed Router ETH Balance Allows Any Caller to Drain Stranded Native ETH via WETH Payment Path — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`PeripheryPayments.pay()` uses `address(this).balance` — the router's entire persistent native ETH balance — to settle WETH swap obligations without any per-caller attribution. ETH strands on the router whenever a user calls a `payable` entry point with excess `msg.value` and omits `refundETH()`. Any subsequent caller who initiates a WETH swap with `msg.value = 0` has their pool obligation silently covered by the victim's stranded ETH, receiving a free swap while the victim loses principal.

## Finding Description
`PeripheryPayments.pay()` (L69–88) contains a WETH-specific branch that reads `address(this).balance` without any per-transaction or per-caller scoping:

```solidity
uint256 nativeBalance = address(this).balance;
if (nativeBalance >= value) {
    IWETH9(WETH).deposit{value: value}();
    IERC20(WETH).safeTransfer(recipient, value);
} else if (nativeBalance > 0) {
    ...
    IERC20(WETH).safeTransferFrom(payer, recipient, value - nativeBalance);
} else {
    IERC20(WETH).safeTransferFrom(payer, recipient, value);
}
```

`address(this).balance` is the router's **persistent** balance across all transactions. ETH accumulates on the router whenever a user calls any `payable` entry point (e.g., `exactInputSingle` at L67, `exactInput` at L92, `exactOutputSingle` at L130, `exactOutput` at L154) with `msg.value` exceeding the swap's actual WETH cost and omits `refundETH()`.

The `receive()` guard (L32–34) only blocks plain ETH transfers from non-WETH addresses; it does **not** prevent ETH from accumulating via `msg.value` in payable function calls. Once stranded, that ETH is available to `pay()` for any caller's WETH obligation in any future transaction.

The `TransientCallbackPool` transient storage (used in `MetricOmmSwapRouterBase`) tracks pool, callback mode, payer, token, trades left, and amount in — but there is **no** tracking of `msg.value` or per-caller ETH attribution. The `_getPayer()` call in `_justPayCallback` (L192–199) correctly identifies the original swap initiator as the `payer` argument, but the ETH actually consumed comes from `address(this).balance`, which may belong to a different user entirely. When `nativeBalance >= value`, the `safeTransferFrom(payer, ...)` branch is never reached, so the attacker pays nothing.

The interface NatDoc at `IMetricOmmSimpleRouter.sol` L11 states "No native ETH, WETH wrap/unwrap, on-chain quotes, sweep, or refund helpers" — yet the contract is `payable` and does consume native ETH for WETH. This documentation mismatch increases the likelihood that users will not include `refundETH()` in their multicall.

## Impact Explanation
Direct loss of user principal. A victim who strands ETH on the router loses it to any attacker who subsequently executes a WETH swap. The attacker pays zero ETH (`msg.value = 0`) and zero WETH (no `transferFrom` is triggered when `nativeBalance >= value`), receiving the full swap output at the victim's expense. The loss equals the ETH consumed from the router's balance, bounded only by the attacker's chosen `amountIn`. This satisfies the "Critical/High/Medium direct loss of user principal above Sherlock thresholds" allowed impact gate.

## Likelihood Explanation
Medium. The precondition — ETH stranded on the router — arises whenever a user calls any `payable` swap entry point with `msg.value` exceeding the WETH cost and omits `refundETH()`. The interface NatDoc actively discourages users from expecting ETH handling, making it likely they will not include `refundETH()`. Once ETH is stranded, exploitation requires only a single unprivileged WETH swap call with `msg.value = 0` and no WETH approval. The attack is repeatable across any block after stranding occurs.

## Recommendation
Track per-transaction ETH attribution using transient storage (already used elsewhere via `TransientCallbackPool`). At each `payable` entry point, store `msg.value` credited to the current caller. Inside `pay()`, deduct only from that attributed balance rather than `address(this).balance`. Any unspent attributed ETH should be refunded automatically at the end of the outermost call (or `pay()` should fall through to `safeTransferFrom` when the router's balance cannot be attributed to the current payer). Alternatively, add an automatic `refundETH()` at the end of each swap entry point to prevent ETH from ever stranding.

## Proof of Concept
```
Block N — Victim transaction:
  victim calls exactInputSingle(pool, WETH→X, amountIn=100)
    with msg.value = 200
  Pool calls metricOmmSwapCallback → _justPayCallback → pay(WETH, victim, pool, 100)
    → nativeBalance = address(this).balance = 200 >= 100
    → IWETH9.deposit{value: 100}(); IERC20(WETH).safeTransfer(pool, 100) ✓
    → safeTransferFrom(victim, ...) is NEVER called
  Transaction ends: router.balance = 100 ETH (stranded, no refundETH called)

Block N+1 — Attacker transaction:
  attacker calls exactInputSingle(pool, WETH→Y, amountIn=100)
    with msg.value = 0, no WETH approval
  Pool calls metricOmmSwapCallback → _justPayCallback → pay(WETH, attacker, pool, 100)
    → nativeBalance = address(this).balance = 100 >= 100
    → IWETH9.deposit{value: 100}(); IERC20(WETH).safeTransfer(pool, 100) ✓
    → safeTransferFrom(attacker, ...) is NEVER called
  Attacker receives full swap output; victim's 100 ETH is permanently lost.
```