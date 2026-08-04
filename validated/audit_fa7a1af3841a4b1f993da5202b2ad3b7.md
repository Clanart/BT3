### Title
Single blacklisted/paused output token permanently locks all other escrowed assets in a multi-token intent order - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentsBase._withdraw` releases every token in an order's escrow in one atomic loop. If any single token in that list belongs to a pausable/blacklistable ERC-20 (e.g. USDC, USDT) and the beneficiary is paused/blacklisted, or the token is otherwise reverting, the entire `_withdraw` call reverts — including the release of all *other*, perfectly healthy escrowed tokens in the same order. Because `fillOrder`, `cancel`, and the cross-chain `RedeemEscrow`/`RefundEscrow` handlers all call `_withdraw` with the exact same fixed token list and no per-token isolation or fallback, the order can never be finalized and all its escrowed assets are permanently stuck.

### Finding Description
`_withdraw` iterates over `body.tokens` and performs a `safeTransfer` (or raw ETH `call`) for each token, decrementing `_orders[commitment][token]` first and reverting the whole transaction if any transfer fails: [1](#0-0) 

This is structurally identical to the reported BakerFi `MultiStrategyVault` bug: multiple independent “legs” (strategies there, tokens here) are settled inside a single loop with no way to exclude a broken leg, so one bad leg blocks release of funds from all the others. Here, the escrow being drained is real user/solver funds locked per-order, and every caller of `_withdraw` passes the full order token list with no exclusion mechanism:

- Same-chain full/partial fill release: [2](#0-1) 
- Same-chain cancellation refund: [3](#0-2) 
- Cross-chain redeem/refund escrow (`RequestKind.RedeemEscrow` / `RequestKind.RefundEscrow`) dispatched from the destination chain via Hyperbridge, also routed through `_withdraw`.

Because `_filled[body.commitment] = beneficiary` is set *before* the loop but the whole function reverts on any failed transfer, the finalize flag never actually commits — the order stays open, `_orders[commitment][token]` balances for the healthy tokens remain untouched, and every retry (fill, cancel, or relayed cross-chain redeem) hits the exact same revert on the same poisoned token, since the token list is a fixed input from the original `Order` struct.

An order with, say, one ETH leg and one USDC leg: if the beneficiary or the contract itself later becomes blacklisted on USDC (a routine, permissionless third-party event, not requiring a malicious relayer, admin, or governance actor), the ETH leg becomes permanently unwithdrawable too, since it's bundled in the same atomic call with no per-token skip/retry path.

### Impact Explanation
This is a direct fund-loss/fund-lock bug matching the bounty's "stealing or loss of funds" and "false proof/state acceptance" categories are not applicable, but "loss of funds" clearly is: legitimate escrowed assets (both the user's principal for refunds and the solver's earned input tokens for fills) become permanently inaccessible once any single token in a multi-asset order becomes untransferable. Since orders can mix arbitrary ERC-20s (any of which could later be paused/blacklisted for the beneficiary independent of the gateway), and the gateway offers no mechanism to exclude or skip a single problematic token, this is a systemic, unprivileged-triggerable lock of bridged/escrowed capital.

### Likelihood Explanation
Likelihood is realistic: pausable/blacklistable stablecoins (USDC, USDT) are extremely common order assets, and beneficiary blacklisting is an ordinary compliance action outside the protocol's control — it requires no malicious relayer, prover, or admin. Any order that combines such a token with other assets is exposed. The same class of failure also applies to a token that reverts for other reasons (fee-on-transfer edge cases, temporary pause, contract upgrade in progress), broadening the trigger surface further.

### Recommendation
Decouple per-token release from the atomic finalize step:
- Attempt each token transfer independently (e.g., via a low-level call with try/catch or a separate `_pull`/`_release` per token) and, on failure, keep the escrow entry intact and let it be retried in isolation rather than reverting the whole withdrawal.
- Alternatively, finalize the order (`_filled[commitment] = beneficiary`) unconditionally once escrow accounting is updated, and route any token whose transfer fails into a per-(commitment, token) "stuck funds" claim mapping that the beneficiary (or governance) can retry/redeem later, mirroring the BakerFi report's recommendation to exclude the broken leg rather than block the whole settlement.

### Proof of Concept
1. A user places a cross-chain (or same-chain) order whose `inputs`/escrowed tokens are `[ETH, USDC]`.
2. Order is filled or cancelled, triggering `_withdraw` with `body.tokens = [ETH_leg, USDC_leg]`.
3. Before settlement, the beneficiary address is added to USDC's blacklist (a routine, permissionless third-party compliance action — Circle can blacklist any address at will).
4. `_withdraw` loop reaches the USDC leg: `IERC20(USDC).safeTransfer(beneficiary, amount)` reverts because the beneficiary is blacklisted, per [4](#0-3) .
5. The entire `_withdraw` call reverts, so the ETH leg transfer that already logically "succeeded" in the loop is rolled back along with the `_orders` decrement and the `_filled` finalize write.
6. Every subsequent attempt to fill/cancel/redeem this order re-executes `_withdraw` with the same fixed token list and hits the same USDC revert — the ETH escrow (and the USDC escrow) are now permanently locked, with no code path in `IntentsBase.sol`/`IntrinsicIntents.sol`/`ExtrinsicIntents.sol` to exclude the poisoned USDC leg and release the ETH leg alone.

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L390-410)
```text
    function _withdraw(WithdrawalRequest memory body, bool isRefund, bool finalize) internal {
        address beneficiary = address(uint160(uint256(body.beneficiary)));
        if (finalize) _filled[body.commitment] = beneficiary;

        uint256 len = body.tokens.length;
        for (uint256 i; i < len; i++) {
            address token = address(uint160(uint256(body.tokens[i].token)));
            uint256 amount = body.tokens[i].amount;
            if (amount == 0) continue;

            uint256 escrowed = _orders[body.commitment][token];
            if (escrowed == 0) revert UnknownOrder();

            _orders[body.commitment][token] = escrowed - amount;
            if (token == address(0)) {
                (bool sent,) = beneficiary.call{value: amount}("");
                if (!sent) revert InsufficientNativeToken();
            } else {
                IERC20(token).safeTransfer(beneficiary, amount);
            }
        }
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L131-134)
```text
        WithdrawalRequest memory body = WithdrawalRequest({
            commitment: commitment, tokens: escrowedInputs, beneficiary: bytes32(uint256(uint160(msg.sender)))
        });
        _withdraw(body, false, isFullyFilled);
```

**File:** evm/src/apps/intentsv2/IntrinsicIntents.sol (L183-186)
```text
        WithdrawalRequest memory body =
            WithdrawalRequest({commitment: commitment, tokens: remainingTokens, beneficiary: order.user});

        _withdraw(body, true, true);
```
