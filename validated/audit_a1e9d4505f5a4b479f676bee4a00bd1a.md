### Title
Fast Transfer Relayer Reimbursement Permanently Lost Due to Detached Token Transfer Before State Update in Finalization - (File: `near/omni-bridge/src/lib.rs`)

### Summary
In `process_fin_transfer_to_other_chain` and `utxo_fin_transfer_fast`, the fast transfer state is permanently updated (marked as finalised or removed) while the token reimbursement to the fast relayer is fired with `.detach()` — a fire-and-forget pattern that silently discards failures. If the token transfer fails, the fast transfer is irrecoverably finalised and the relayer's pre-paid funds are permanently locked in the bridge with no recovery path.

### Finding Description

**`process_fin_transfer_to_other_chain`** (lines 2028–2040):

When a fast transfer was previously performed (a relayer pre-paid the user), `fin_transfer_callback` routes to `process_fin_transfer_to_other_chain`. Inside, the code reimburses the fast relayer via `send_tokens(...).detach()` and then immediately calls `mark_fast_transfer_as_finalised(...)`:

```rust
if let Some(relayer) = recipient {
    self.send_tokens(
        token,
        relayer,
        U128(transfer_message.amount_without_fee()...),
        "",
    )
    .detach();                                          // ← fire-and-forget; failure silently ignored
    self.mark_fast_transfer_as_finalised(&fast_transfer.id()); // ← state updated unconditionally
}
```

The `.detach()` call schedules the promise but does not await its result. If `ft_transfer` or `mint` fails (e.g., token contract paused, recipient storage not registered, insufficient gas), the fast transfer is still permanently marked as finalised. No retry is possible because `add_fin_transfer` already inserted the transfer ID into `finalised_transfers` (line 1985), and `mark_fast_transfer_as_finalised` sets `finalised = true` in `fast_transfers`. The relayer's reimbursement is irrecoverably lost.

**`utxo_fin_transfer_fast`** (lines 2518–2561):

The same pattern appears here. The fast transfer state is updated *before* the detached token send:

```rust
let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
    self.remove_fast_transfer(&fast_transfer.id());   // ← state destroyed before send
    fast_transfer.amount
} else {
    self.mark_fast_transfer_as_finalised(&fast_transfer.id()); // ← state finalised before send
    U128(fast_transfer.amount_without_fee()...)
};

self.send_tokens(fast_transfer.token_id.clone(), fast_transfer_status.relayer, amount, "")
    .detach();  // ← fire-and-forget
```

The code itself acknowledges the unresolved problem at line 2484:
```rust
// TODO: check how to deal with failed send_tokens
```

In both branches, if `send_tokens` fails, the fast transfer record is either permanently deleted or permanently finalised, with no mechanism to retry or recover the relayer's funds.

### Impact Explanation

**Critical/High — Permanent freezing and irrecoverable lock of protocol funds.**

The fast relayer pre-paid the user out of their own balance. Upon `fin_transfer` finalization, the bridge is supposed to reimburse the relayer from its locked token balance. If the reimbursement transfer fails silently:

1. The fast transfer is permanently marked finalised — `add_fin_transfer` prevents re-finalization (`BridgeError::TransferAlreadyFinalised`).
2. The fast transfer status is permanently finalised — `mark_fast_transfer_as_finalised` sets `finalised = true`, blocking any future claim.
3. The relayer's pre-paid tokens remain locked in the bridge with no recovery path.
4. The bridge's `locked_tokens` accounting is already decremented (via `unlock_tokens_if_needed` at line 1997), so the accounting is permanently desynchronised from actual balances.

### Likelihood Explanation

**Medium.** Token transfers can fail for realistic reasons:
- The token contract is paused at the moment of execution.
- The fast relayer's account does not have storage registered for the token (required for `ft_transfer`).
- Insufficient gas allocated to the detached promise (the `send_tokens` function computes gas dynamically from remaining gas, and with `.detach()` there is no guarantee of adequate allocation).
- The bridge contract's token balance is temporarily insufficient due to concurrent operations.

The `// TODO: check how to deal with failed send_tokens` comment at line 2484 confirms the developers are aware this failure path is unhandled.

### Recommendation

Replace the `.detach()` pattern with a proper callback that checks the transfer result. If the transfer fails, revert the state:

- In `process_fin_transfer_to_other_chain`: do not call `mark_fast_transfer_as_finalised` until a callback confirms the `send_tokens` succeeded. On failure, revert `unlock_tokens_if_needed` and leave the fast transfer in its pre-finalised state.
- In `utxo_fin_transfer_fast`: do not call `remove_fast_transfer` or `mark_fast_transfer_as_finalised` until a callback confirms success. On failure, restore the fast transfer record.

### Proof of Concept

**Scenario for `process_fin_transfer_to_other_chain`:**

1. Fast relayer calls `ft_transfer_call` on the token contract, sending tokens to the bridge with a `FastFinTransfer` message. The bridge records the fast transfer via `add_fast_transfer` (line 941).
2. A trusted relayer calls `fin_transfer` with a valid proof of the original `InitTransfer` from the origin chain.
3. `fin_transfer_callback` decodes the proof and routes to `process_fin_transfer_to_other_chain` (line 743).
4. `add_fin_transfer` inserts the transfer ID into `finalised_transfers` (line 1985) — replay protection is now set.
5. `unlock_tokens_if_needed` decrements `locked_tokens` for the origin chain (line 1997).
6. The fast transfer status is found; `send_tokens(token, relayer, amount, "").detach()` is called (line 2029).
7. The token contract's `ft_transfer` fails (e.g., contract paused, storage not registered).
8. `mark_fast_transfer_as_finalised` executes unconditionally (line 2040), setting `finalised = true`.
9. The fast relayer's reimbursement is permanently lost. The fast transfer cannot be retried (finalised). The `locked_tokens` balance is permanently understated. The relayer's pre-paid funds are irrecoverably locked in the bridge. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1985-1985)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
```

**File:** near/omni-bridge/src/lib.rs (L2027-2041)
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
        } else {
```

**File:** near/omni-bridge/src/lib.rs (L2270-2277)
```rust
    fn mark_fast_transfer_as_finalised(&mut self, fast_transfer_id: &FastTransferId) {
        let mut status = self
            .get_fast_transfer_status(fast_transfer_id)
            .near_expect(BridgeError::FastTransferNotFound);
        status.finalised = true;
        self.fast_transfers
            .insert(fast_transfer_id, &FastTransferStatusStorage::V0(status));
    }
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
