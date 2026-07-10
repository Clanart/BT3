### Title
Detached Token Transfer on Fast-Transfer Relayer Repayment Causes Irrecoverable Loss of Fronted Funds on Failure - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

In `process_fin_transfer_to_other_chain` and `utxo_fin_transfer_fast`, when a fast transfer is finalized the bridge first mutates persistent state (marks the fast transfer as finalised or removes it) and then dispatches the relayer-repayment token transfer using `.detach()`. `.detach()` is NEAR's exact analog of a Solidity try-catch that swallows all failures: if the underlying `ft_transfer` or `mint` panics, the promise failure is silently discarded. Because the state mutation already committed, the fast-transfer record is permanently consumed with no recovery path, and the relayer's fronted tokens are irrecoverably lost.

---

### Finding Description

**`process_fin_transfer_to_other_chain`** (called when a cross-chain proof finalizes a transfer that had a prior fast-transfer):

```
// line 2040 – state mutated first
self.mark_fast_transfer_as_finalised(&fast_transfer.id());

// line 2029-2039 – repayment fired with no failure handler
self.send_tokens(token, relayer, amount, "").detach();
```

`mark_fast_transfer_as_finalised` sets `status.finalised = true` in persistent storage. The subsequent `.detach()` call means any panic inside `send_tokens` (e.g., `ft_transfer` panicking because the relayer has no storage registration for the token) is silently swallowed. The fast-transfer entry is now permanently finalised; there is no callback, no revert, and no re-entry point.

**`utxo_fin_transfer_fast`** (called when a UTXO proof arrives for a fast-transferred UTXO):

```
// lines 2530-2533 – state mutated first (remove or mark finalised)
let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
    self.remove_fast_transfer(&fast_transfer.id());   // permanent removal
    ...
} else {
    self.mark_fast_transfer_as_finalised(&fast_transfer.id());
    ...
};

// lines 2542-2548 – repayment fired with no failure handler
self.send_tokens(fast_transfer.token_id.clone(), fast_transfer_status.relayer, amount, "")
    .detach();
```

Same pattern: state is committed before the token transfer, and `.detach()` silently discards any failure.

**Why `send_tokens` can fail silently:**

`send_tokens` dispatches one of:
- `ft_transfer` (non-deployed token, empty msg) — panics if recipient has no storage registration
- `mint` (deployed/bridged token) — can panic if the token contract is paused or has a bug
- `near_withdraw` → `near_withdraw_callback` (wNEAR) — `near_withdraw_callback` explicitly panics on failure, but that panic is swallowed by `.detach()`

The developers' own security checklist in `near/CLAUDE.md` line 228 states: **"Check .detach() usage: Detached promises should only be used for non-critical operations."** Relayer repayment of fronted funds is unambiguously a critical operation.

---

### Impact Explanation

When the detached `send_tokens` promise fails:

1. The fast-transfer record is already finalised/removed — it cannot be re-submitted or re-processed.
2. The relayer's fronted tokens (which they transferred to the recipient on NEAR ahead of the proof) are permanently unrecoverable.
3. No `FailedFinTransferEvent` is emitted; the bridge emits a success event (`FinTransferEvent`) regardless.
4. The bridge's internal `locked_tokens` accounting is already updated (unlocked on origin chain, locked on destination chain) before the failed transfer, creating a permanent accounting discrepancy between on-chain token balances and the bridge's internal ledger.

This matches: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

Conditions that cause `ft_transfer` to fail silently:

- The relayer's account loses its storage registration for the specific token (e.g., the relayer calls `storage_unregister` on the token contract between fronting the fast transfer and the proof arriving).
- The token contract is paused or upgraded in a way that rejects transfers during the window between fast-transfer execution and proof finalization.
- For wNEAR: the wNEAR contract's `near_withdraw` fails (e.g., insufficient NEAR balance in the wNEAR contract).

These are low-frequency but realistic operational conditions, not theoretical-only. The window between fast-transfer execution and proof arrival can be minutes to hours, during which state can change. The impact when triggered is total and unrecoverable.

---

### Recommendation

Replace `.detach()` with a proper callback for the relayer repayment in both `process_fin_transfer_to_other_chain` and `utxo_fin_transfer_fast`. The callback should:

1. On success: emit the success event and finalize state.
2. On failure: revert the fast-transfer state mutation (un-finalise or re-insert the record) so the repayment can be retried.

```rust
// Instead of:
self.send_tokens(token, relayer, amount, "").detach();
self.mark_fast_transfer_as_finalised(&fast_transfer.id());

// Do:
self.send_tokens(token, relayer, amount, "")
    .then(
        Self::ext(env::current_account_id())
            .with_static_gas(REPAY_RELAYER_CALLBACK_GAS)
            .repay_relayer_callback(&fast_transfer.id(), transfer_message),
    );
// Move mark_fast_transfer_as_finalised into the success branch of the callback
```

---

### Proof of Concept

1. Relayer R fronts 1000 USDC to user U on NEAR via `fast_fin_transfer` (EVM → NEAR fast transfer). The fast-transfer record is stored with `status.finalised = false`.
2. R subsequently calls `storage_unregister` on the USDC token contract (or the token contract is paused by its admin).
3. The EVM-side proof arrives; a trusted relayer calls `fin_transfer` → `fin_transfer_callback` → `process_fin_transfer_to_other_chain`.
4. Line 2040: `mark_fast_transfer_as_finalised` sets `status.finalised = true` in persistent storage.
5. Line 2029-2039: `send_tokens(token, R, 1000, "").detach()` dispatches `ft_transfer(R, 1000)`.
6. `ft_transfer` panics (R has no storage). The panic is silently discarded by `.detach()`.
7. R has permanently lost 1000 USDC. The fast-transfer record is finalised and cannot be re-submitted. The bridge emits `FinTransferEvent` as if everything succeeded. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** near/omni-bridge/src/lib.rs (L2027-2040)
```rust
        // If fast transfer happened, send tokens to the relayer that executed fast transfer
        if let Some(relayer) = recipient {
            self.send_tokens(
                token,
                relayer,
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
                "",
            )
            .detach();
            self.mark_fast_transfer_as_finalised(&fast_transfer.id());
```

**File:** near/omni-bridge/src/lib.rs (L2102-2106)
```rust
        } else if msg.is_empty() {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
```

**File:** near/omni-bridge/src/lib.rs (L2518-2548)
```rust
    fn utxo_fin_transfer_fast(
        &mut self,
        fast_transfer: FastTransfer,
        fast_transfer_status: FastTransferStatus,
        utxo_fin_transfer_msg: UtxoFinTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            !fast_transfer_status.finalised,
            BridgeError::FastTransferAlreadyFinalised.as_ref()
        );

        let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
            self.remove_fast_transfer(&fast_transfer.id());
            fast_transfer.amount
        } else {
            self.mark_fast_transfer_as_finalised(&fast_transfer.id());
            // With transfers to other chain the fee will be claimed after finalization on the destination chain
            U128(
                fast_transfer
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            )
        };

        self.send_tokens(
            fast_transfer.token_id.clone(),
            fast_transfer_status.relayer,
            amount,
            "",
        )
        .detach();
```

**File:** near/CLAUDE.md (L228-228)
```markdown
4. **Check .detach() usage**: Detached promises should only be used for non-critical operations
```
