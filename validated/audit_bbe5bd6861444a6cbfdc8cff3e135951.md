Audit Report

## Title
Unprotected `unwrapWETH9` allows any caller to drain all WETH stranded on the router as ETH — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
`unwrapWETH9` is `public payable` with no `msg.sender` or ownership check. It reads the router's full WETH balance and transfers it as ETH to a fully attacker-controlled `recipient`. Any WETH that reaches the router and is not consumed atomically in the same `multicall` is immediately stealable by any third party calling `unwrapWETH9(0, attacker)`.

## Finding Description
The root cause is in `PeripheryPayments.unwrapWETH9` (lines 37–45):

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    uint256 balanceWETH = IERC20(WETH).balanceOf(address(this));
    if (balanceWETH < amountMinimum) revert InsufficientWETH(amountMinimum, balanceWETH);
    if (balanceWETH > 0) {
        IWETH9(WETH).withdraw(balanceWETH);
        _transferETH(recipient, balanceWETH);
    }
}
```

There is no check that `recipient == msg.sender` or any other caller-binding guard. The only guard is the `amountMinimum` floor, which an attacker bypasses by passing `0`.

**Stranding path:** `exactInputSingle` (line 74) passes `params.recipient` directly to the pool's `swap` call with no restriction preventing `params.recipient == address(router)`. A user who calls `exactInputSingle` standalone (outside a `multicall`) with `recipient: address(router)` completes the swap and leaves WETH on the router across a transaction boundary. The same applies to `exactInput` (line 106) and `exactOutputSingle`/`exactOutput`. WETH can also be stranded by a direct ERC-20 `transfer` to the router; the `receive()` guard (lines 32–34) only blocks native ETH from non-WETH senders, not WETH token transfers.

**Exploit:** Once WETH is stranded, any EOA calls `router.unwrapWETH9(0, attacker)`. The function calls `IWETH9(WETH).withdraw(balanceWETH)` then `_transferETH(attacker, balanceWETH)`, sending 100% of the router's WETH balance as ETH to the attacker. The victim receives nothing.

**Contrast with `refundETH`:** The sibling function (lines 58–63) correctly hard-codes `msg.sender` as the recipient and is not exploitable. `unwrapWETH9` and `sweepToken` lack this binding.

## Impact Explanation
Direct, complete loss of user principal. The attacker receives 100% of stranded WETH as ETH; the victim's funds are permanently lost. This is a direct loss of user principal meeting the Sherlock High threshold.

## Likelihood Explanation
The stranding precondition is realistic and reachable by any unprivileged caller:
1. The router's swap entry points publicly accept `recipient: address(router)` — this is the documented pattern for the WETH-unwrap flow — with no guard preventing standalone (non-multicall) use.
2. A user who calls any swap function standalone with `recipient: address(router)` strands WETH between transactions.
3. WETH can also be stranded by any direct ERC-20 `transfer` to the router address.
4. An attacker needs only to monitor the router's WETH balance (on-chain read or mempool observation) and call `unwrapWETH9(0, attacker)` — no special privilege, no front-running complexity beyond a simple balance poll.

## Recommendation
Bind the recipient to `msg.sender` in both `unwrapWETH9` and `sweepToken`, matching the pattern already used in `refundETH`:

```solidity
function unwrapWETH9(uint256 amountMinimum, address recipient) public payable override {
    require(recipient == msg.sender, "recipient must be caller");
    // ... rest unchanged
}
```

Alternatively, remove the `recipient` parameter entirely and always send to `msg.sender`, or use transient storage to track per-caller WETH attribution and only allow each caller to withdraw their own balance.

## Proof of Concept
1. **Strand WETH:** Victim calls `router.exactInputSingle(ExactInputSingleParams{..., recipient: address(router), ...})` as a standalone transaction. The pool swap completes; WETH lands on the router.
2. **Steal:** Attacker calls `router.unwrapWETH9(0, attacker)` from any EOA.
3. **Result:** `IWETH9(WETH).withdraw(balanceWETH)` converts all router WETH to ETH; `_transferETH(attacker, balanceWETH)` sends it to the attacker. Victim receives zero. Router WETH and ETH balances are both zero.

A Foundry test can reproduce this by: (a) pranking a victim to call `exactInputSingle` with `recipient: address(router)` outside a multicall, (b) asserting `weth.balanceOf(address(router)) > 0`, (c) pranking an attacker to call `unwrapWETH9(0, attacker)`, and (d) asserting the attacker's ETH balance increased by the full stranded amount and the router's WETH balance is zero.