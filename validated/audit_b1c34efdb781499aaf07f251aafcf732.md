### Title
Detached `send_tokens` With No Failure Callback Permanently Locks Relayer Reimbursement Funds in Fast-Transfer Finalization - (File: `near/omni-bridge/src/lib.rs`)

### Summary
In `process_fin_transfer_to_other_chain` and `utxo_fin_transfer_fast`, when a fast transfer is finalized, the bridge calls `send_tokens(...).detach()` to reimburse the relayer. The fast transfer state is updated synchronously (removed or marked finalised) before the token transfer completes. If the token transfer fails — for example because the relayer's account has no storage registered for the token — the tokens are permanently locked in the bridge contract with no recovery path. The codebase itself acknowledges this gap with an explicit `// TODO: check how to deal with failed send_tokens` comment.

### Finding Description

**Root cause — `process_fin_transfer_to_other_chain`:**

When a cross-chain transfer (chain A → NEAR → chain B) is finalized and a fast transfer exists, the bridge reimburses the relayer:

```rust
// near/omni-bridge/src/lib.rs lines 2028-2040
if let Some(relayer) = recipient {
    self.send_tokens(
        token,
        relayer,
        U128(transfer_message.amount_without_fee()...),
        "",
    )
    .detach();                                          // ← no callback
    self.mark_fast_transfer_as_finalised(&fast_transfer.id());
}
```

`send_tokens` is detached; execution continues synchronously to `mark_fast_transfer_as_finalised`. If the underlying `ft_transfer` (for non-deployed tokens) or `mint` (for deployed tokens) fails, the fast transfer is already finalised and cannot be retried.

**Root cause — `utxo_fin_transfer_fast`:**

The same pattern appears in the UTXO fast-transfer path, where the state is updated *before* `send_tokens` is even called:

```rust
// near/omni-bridge/src/lib.rs lines 2518-2548
let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
    self.remove_fast_transfer(&fast_transfer.id());   // ← state cleared first
    fast_transfer.amount
} else {
    self.mark_fast_transfer_as_finalised(&fast_transfer.id()); // ← state cleared first
    U128(fast_transfer.amount_without_fee()...)
};

self.send_tokens(
    fast_transfer.token_id.clone(),
    fast_transfer_status.relayer,
    amount,
    "",
)
.detach();                                             // ← no callback
```

The caller of `utxo_fin_transfer_fast` even carries an explicit acknowledgement of the unresolved failure case:

```rust
// near/omni-bridge/src/lib.rs line 2484
// TODO: check how to deal with failed send_tokens
return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
```

**Failure trigger — `send_tokens` for non-deployed tokens:**

```rust
// near/omni-bridge/src/lib.rs lines 2102-2106
} else if msg.is_empty() {
    ext_token::ext(token)
        .with_attached_deposit(ONE_YOCTO)
        .with_static_gas(FT_TRANSFER_GAS)
        .ft_transfer(recipient, amount, None)   // ← fails if recipient has no storage
```

NEP-141 `ft_transfer` panics if the recipient has no storage registration. Because the promise is detached, this panic is silently swallowed. The tokens remain in the bridge contract and the fast transfer record is already gone.

### Impact Explanation

For non-deployed tokens (native assets locked in the bridge), a failed `ft_transfer` leaves the tokens stranded in the bridge contract. The fast transfer record has been removed or finalised, so no retry is possible and no DAO rescue function exists. The relayer who pre-paid the user on NEAR loses their reimbursement permanently, and the corresponding token balance is irrecoverably locked in the bridge. This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

A relayer must not have storage registered for the token at the moment of finalization. Concrete scenarios:

1. The relayer calls `storage_unregister` on the token contract between performing the fast transfer and the UTXO connector calling `utxo_fin_transfer`.
2. The relayer's NEAR account is deleted and re-created (storage registration is lost).
3. The relayer performed the fast transfer for a token they hold but whose storage they later withdrew.

The `// TODO` comment in the production code confirms the developers are aware the failure path is unhandled.

### Recommendation

Replace the fire-and-forget `.detach()` pattern with a proper callback that reverts the fast transfer state on failure:

```rust
self.send_tokens(token, relayer, amount, "")
    .then(
        Self::ext(env::current_account_id())
            .with_static_gas(FAST_TRANSFER_REIMBURSE_CALLBACK_GAS)
            .fast_transfer_reimburse_callback(
                &fast_transfer.id(),
                token,
                relayer,
                amount,
            ),
    );
// Do NOT mark as finalised here; do it inside the success branch of the callback.
```

In the callback, if the promise failed, un-finalise (or re-insert) the fast transfer so the reimbursement can be retried after the relayer registers storage.

### Proof of Concept

**UTXO fast-transfer path:**

1. Relayer registers storage for `btc-token.near` and performs a fast transfer: sends `amount` tokens to the bridge via `ft_transfer_call` with a `FastFinTransfer` message. The bridge records `FastTransferStatus { relayer: relayer.near, finalised: false }`.
2. Relayer calls `storage_unregister` on `btc-token.near`, withdrawing their storage deposit.
3. The BTC connector calls `ft_transfer_call` on `btc-token.near` → `ft_on_transfer` → `utxo_fin_transfer` → `utxo_fin_transfer_fast`.
4. Inside `utxo_fin_transfer_fast`, `self.remove_fast_transfer(...)` is called — the fast transfer record is deleted.
5. `send_tokens(btc-token.near, relayer.near, amount, "").detach()` is scheduled.
6. The `ft_transfer` to `relayer.near` panics because `relayer.near` has no storage registration.
7. The panic is silently swallowed (detached promise).
8. `amount` tokens remain in the bridge contract. The fast transfer record is gone. The relayer has no recourse. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** near/omni-bridge/src/lib.rs (L2028-2040)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L2483-2486)
```rust
        if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            // TODO: check how to deal with failed send_tokens
            return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
        }
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
