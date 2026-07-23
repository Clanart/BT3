Audit Report

## Title
Router `pay()` Drains Accumulated ETH via `address(this).balance` in WETH Branch — (`metric-periphery/contracts/base/PeripheryPayments.sol`)

## Summary
The `pay()` internal helper in `PeripheryPayments` uses `address(this).balance` — the router's **total** ETH balance — rather than only the ETH contributed by the current transaction. Any ETH left in the router by a prior user (e.g., excess `msg.value` not reclaimed via `refundETH`) is silently consumed to fund a subsequent user's WETH swap, causing direct, unrecoverable loss of the prior user's ETH principal.

## Finding Description
In `PeripheryPayments.sol` L73–84, the WETH branch of `pay()` reads `address(this).balance` as `nativeBalance`:

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

`address(this).balance` is the **aggregate** ETH held by the router at callback time, not the ETH contributed by the current call. All entry-points (`exactInputSingle`, `exactInput`, `exactOutputSingle`, `exactOutput`, `multicall`) are `payable`, so users routinely send ETH with their call and are expected to reclaim surplus via `refundETH()`. If they omit that step — or if a multicall batch is constructed without a trailing `refundETH` — the surplus ETH persists in the router across transactions.

The `receive()` guard at L32–34 only blocks direct ETH pushes from non-WETH addresses; it does not prevent ETH from accumulating via `msg.value` in payable swap calls.

The `refundETH()` function at L57–63 sends `address(this).balance` to whoever calls it first, so once a subsequent user's swap has consumed the residual ETH, the original depositor cannot recover it.

The exploit path through `exactInputSingle` (L67–86) sets `msg.sender` as the payer via `_setNextCallbackContext`, then the pool callback fires `_justPayCallback` → `pay(WETH, payer, pool, value)`. When `nativeBalance >= value`, the `safeTransferFrom(payer, ...)` branch is never reached — the router wraps its own accumulated ETH instead of pulling from the actual payer.

## Impact Explanation
**Direct loss of user ETH principal.** Any ETH left in the router by User A is consumed — in full (`nativeBalance >= value` branch) or in part (`nativeBalance > 0` branch) — to settle User B's WETH swap obligation. User B's wallet is debited only for the shortfall (`value - nativeBalance`), while User A's ETH is permanently transferred to the pool on User B's behalf. This constitutes a direct, unrecoverable loss of user principal, meeting the Critical/High threshold under the allowed impact gate.

## Likelihood Explanation
**Medium–High.** The router is designed for `multicall` batching (permit → swap → refundETH). Any user who calls a payable swap function directly with ETH, or whose multicall batch omits `refundETH`, leaves ETH in the router. The accumulated balance is publicly visible on-chain. An attacker needs only to observe a non-zero router ETH balance and submit a WETH swap with `msg.value = 0`; no special permissions, privileged roles, or complex setup are required. The attack is repeatable as long as ETH accumulates in the router.

## Recommendation
Track only the ETH contributed by the current call rather than the contract's total balance. The standard fix is to pass `msg.value` down through the call stack (or store it transiently at entry) and compare against that value instead of `address(this).balance`. Alternatively, remove the partial-balance hybrid branches entirely: if the router holds insufficient native ETH for the full `value`, fall through directly to `safeTransferFrom` without consuming any partial contract balance, and document that users must always pair ETH-input swaps with `refundETH` in the same multicall.

## Proof of Concept
1. **Setup**: Router holds 0 ETH. Alice calls `exactInputSingle{value: 2 ether}` with `tokenIn = WETH`, `amountIn = 1 ether`. The pool callback fires `pay(WETH, Alice, pool, 1 ether)`. `nativeBalance = 2 ETH ≥ 1 ETH`, so the router wraps 1 ETH and sends WETH to the pool. Alice receives her output token. **1 ETH remains in the router** because Alice did not call `refundETH`.

2. **Attack**: Bob calls `exactInputSingle{value: 0}` with `tokenIn = WETH`, `amountIn = 1 ether`. The pool callback fires `pay(WETH, Bob, pool, 1 ether)`. `nativeBalance = 1 ETH ≥ 1 ETH`, so the router wraps Alice's residual 1 ETH and sends WETH to the pool. Bob receives his output token **having paid 0 ETH from his own wallet**. Alice's 1 ETH is permanently lost.

3. **Partial drain variant**: Router holds 0.3 ETH (residual from a prior user). Bob calls `exactInputSingle{value: 0}` with `amountIn = 1 ether`. `nativeBalance (0.3) > 0` and `< 1`, so the router wraps 0.3 ETH from the contract and pulls only 0.7 ETH from Bob via `safeTransferFrom`. Bob pays 0.7 ETH instead of 1 ETH; the prior user's 0.3 ETH is stolen.