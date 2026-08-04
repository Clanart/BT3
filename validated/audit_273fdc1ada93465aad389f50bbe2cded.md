Based on the evidence gathered, I found a solid local analog to the "bid tokens aren't enforced to be the same" class of bug: a monetary amount is recorded without binding it to a specific token address, and is later paid out using a token reference resolved independently at a different point in time.

### Title
Solver/fee-token drift between order-fee collection and escrow withdrawal payout can misdirect stale fee accruals - (File: `evm/src/apps/intentsv2/IntentsBase.sol`)

### Summary
`IntentGatewayV2` collects each order's `fees` in "the protocol fee token" at `placeOrder` time, swapping from native token via Uniswap V2 if necessary, and records only a raw `uint256` amount under `_orders[commitment][TRANSACTION_FEES]` — with no token identifier attached to that stored value. [1](#0-0)  When the order is later settled, `_withdraw()` pays out that raw amount by reading `IDispatcher(host()).feeToken()` **live, at withdrawal time**, and transferring that many units of whatever token address the host currently reports as its fee token: [2](#0-1) 

### Finding Description
The escrowed transaction-fee amount and the token it is denominated in are specified in two different places at two different times, exactly mirroring the reported bug class: a quantity is priced/collected against one token context, but a separate later lookup determines which token contract actually receives/sends that quantity, with nothing on-chain binding the two together.

- At `placeOrder`, the fee amount is computed and collected in "the protocol fee token" of that moment (via `getFeeTokenWithDecimals`/host `feeToken()` at that block), and stored as a bare number in `_orders[commitment][TRANSACTION_FEES]`. [1](#0-0) 
- At settlement (`_withdraw`, invoked from both same-chain fills and the cross-chain `RedeemEscrow`/`RefundEscrow` `onAccept` path), the same stored number is transferred using `IDispatcher(host()).feeToken()` resolved fresh at that call — not the token that was actually collected. [2](#0-1) 
- The codebase itself acknowledges that the host's fee token address is not immutable: `BandwidthManager.sol`'s `Withdrawal` struct explicitly names the `token` field "so stale fee-token balances after a host-side swap can still be drained," confirming that a "host-side swap" of the fee token is an anticipated, non-malicious event in this system. [3](#0-2) 

If the host's fee token is updated between when an order's `fees` were collected and when that order is finally settled/withdrawn (any order that sits open across such a change — including partial-fill orders or cross-chain orders awaiting relayer delivery), `_withdraw` silently pays out the *stale numeric amount* in the *new* fee token's units and contract address. This is the identical failure mode Spearbit flagged in Atlas: two independently-specified "token" references for what should be one value, with no on-chain equality check joining them.

### Impact Explanation
This can cause direct loss or misdirection of protocol/user funds:
- If the new fee token is more valuable per unit than the original, the beneficiary receives an unintended windfall paid out of the protocol's real balance of the new token — silent value leakage.
- If the new fee token is less valuable or has different decimals, users/protocol lose the value they were actually owed.
- If the contract holds no balance of the new fee token, `_withdraw` reverts, permanently blocking finalization (denial of settlement) for that commitment, since `_withdraw` is invoked unconditionally on the `finalize` path for every completed/cancelled order. [2](#0-1) 

This falls squarely within "bridged assets... must move exactly once and only to the rightful beneficiary and amount" — here neither the beneficiary nor the amount is wrong by attacker action, but the *token* paid is wrong due to the unenforced binding, causing fund loss/misdirection.

### Likelihood Explanation
Triggering requires only that the host's fee token is changed (a legitimate, documented governance operation referenced by the `BandwidthManager.sol` comments) while at least one order with accrued `TRANSACTION_FEES` remains unsettled — no malicious relayer, prover, or admin key compromise is required, only ordinary timing between a governance fee-token update and normal order lifecycle completion. Cross-chain orders, whose settlement depends on relayer delivery of `RedeemEscrow`/`RefundEscrow` messages, are especially exposed since they can remain pending for extended periods.

### Recommendation
Store the fee token address alongside the amount at the time fees are collected (e.g., key `_orders[commitment][TRANSACTION_FEES]` by the actual token address used, or store a `(token, amount)` pair), and have `_withdraw` transfer using that recorded token rather than re-resolving `IDispatcher(host()).feeToken()` at withdrawal time. This mirrors the Atlas fix pattern: bind the token identity to the value at the point of commitment rather than trusting a second, independently-mutable reference to still agree with it.

### Proof of Concept
1. User calls `placeOrder`; gateway collects `order.fees` in fee token `A` (current `IDispatcher(host()).feeToken()`), recording amount `X` under `_orders[commitment][TRANSACTION_FEES]`.
2. Before the order is filled/settled, Hyperbridge governance updates the host's fee token to `B` (an operation the codebase itself anticipates, per the `BandwidthManager.sol` `Withdrawal.token` comment).
3. A solver later fills/finalizes the order (or the cross-chain `RedeemEscrow` message arrives), triggering `_withdraw` with `finalize = true`.
4. `_withdraw` reads `IDispatcher(host()).feeToken()` → now token `B`, and calls `IERC20(B).safeTransfer(beneficiary, X)` — transferring `X` units of an unrelated token `B` (using accounting that was priced against token `A`), either overpaying/underpaying value or reverting if the contract lacks a `B` balance, per [2](#0-1) .

### Citations

**File:** evm/src/apps/intentsv2/IntentsBase.sol (L412-417)
```text
        if (finalize) {
            uint256 fees = _orders[body.commitment][TRANSACTION_FEES];
            if (fees > 0) {
                delete _orders[body.commitment][TRANSACTION_FEES];
                IERC20(IDispatcher(host()).feeToken()).safeTransfer(beneficiary, fees);
            }
```

**File:** evm/src/apps/BandwidthManager.sol (L50-57)
```text
/// Payload of a `Withdraw` governance message — recovers `amount` of
/// `token` to `beneficiary`. `token` is named explicitly so stale
/// fee-token balances after a host-side swap can still be drained.
struct Withdrawal {
    address token;
    address beneficiary;
    uint256 amount;
}
```
