The vulnerability is real. Let me confirm the complete state machine before writing the finding.

The critical gap is in `utxo_fin_transfer_fast` at line 2529–2530: when the destination is NEAR, it calls `remove_fast_transfer` (erasing the `fast_transfers` entry) but **never** calls `add_fin_utxo_transfer` (so `finalised_utxo_transfers` stays empty). After that, `is_unified_transfer_finalised` at line 1481 returns `false` for the same UTXO `UnifiedTransferId`, and `add_fast_transfer` at line 2253 succeeds because the key was removed — allowing a full second settlement.

---

### Title
Missing `finalised_utxo_transfers` insertion in `utxo_fin_transfer_fast` (NEAR destination) enables double-settlement via `fast_fin_transfer` — (`near/omni-bridge/src/lib.rs`)

### Summary
When a UTXO-origin fast transfer targeting a NEAR recipient is settled by the UTXO connector, `utxo_fin_transfer_fast` removes the `fast_transfers` entry but never records the transfer in `finalised_utxo_transfers`. Because `fast_fin_transfer`'s replay guard (`is_unified_transfer_finalised`) only checks `finalised_utxo_transfers` for UTXO-kind IDs, and `add_fast_transfer` only blocks re-insertion when the key already exists, a malicious trusted relayer can immediately re-submit the same `fast_fin_transfer` call and receive a second token payout to an arbitrary recipient.

### Finding Description

**Step 1 — Initial fast transfer (normal operation)**

A trusted relayer calls `fast_fin_transfer` (via `ft_on_transfer`) with a `FastFinTransferMsg` whose `transfer_id` is a UTXO-kind `UnifiedTransferId` and whose `recipient` is a NEAR address. [1](#0-0) 

`is_unified_transfer_finalised` returns `false` (nothing in `finalised_utxo_transfers` yet). [2](#0-1) 

`add_fast_transfer` inserts the entry into `fast_transfers` with `finalised: false`. [3](#0-2) 

Tokens are sent to the recipient. The `fast_transfers` entry persists.

**Step 2 — UTXO connector settles the transfer**

The UTXO connector calls `utxo_fin_transfer`. The contract finds the existing fast-transfer status and routes to `utxo_fin_transfer_fast`. [4](#0-3) 

Inside `utxo_fin_transfer_fast`, because the destination is NEAR, the code takes the left branch: it calls `remove_fast_transfer` (erasing the `fast_transfers` entry) and returns the full amount to the relayer. [5](#0-4) 

**Crucially, `add_fin_utxo_transfer` is never called.** The `finalised_utxo_transfers` set remains empty for this `UnifiedTransferId`. Compare with the non-fast path, which always calls `add_fin_utxo_transfer`: [6](#0-5) 

**Step 3 — Second `fast_fin_transfer` for the same UTXO transfer_id**

The relayer immediately re-submits `fast_fin_transfer` with the identical UTXO `UnifiedTransferId`:

- `is_unified_transfer_finalised` → checks `finalised_utxo_transfers.contains(transfer_id)` → **`false`** (never inserted). [7](#0-6) 
- `add_fast_transfer` → `fast_transfers.insert(...)` → **succeeds** (key was removed in step 2). [8](#0-7) 
- `send_tokens` fires again, minting or transferring tokens to the relayer-controlled recipient a second time. [9](#0-8) 

### Impact Explanation
Each UTXO origin event must settle at most once. After the fast path for a NEAR-destination UTXO transfer completes, the bridge holds no permanent record of that settlement. A malicious trusted relayer can re-execute `fast_fin_transfer` an unbounded number of times for the same UTXO, each time minting or transferring tokens to an arbitrary NEAR recipient. This directly creates unbacked token supply (for deployed/mintable tokens) or drains the bridge's locked token balance (for non-deployed tokens), constituting a critical double-spend.

### Likelihood Explanation
Any registered trusted relayer can trigger this. The relayer does not need any admin role — only the `is_trusted_relayer` check at line 756 must pass, which is satisfied by normal relayer registration. The attack requires no external oracle, no proof forgery, and no colluding party. The window opens the moment `utxo_fin_transfer` is processed and closes only if the protocol is paused. Likelihood is **high** for any malicious registered relayer.

### Recommendation
In `utxo_fin_transfer_fast`, when the destination chain is NEAR, call `add_fin_utxo_transfer` before (or instead of only) `remove_fast_transfer`. This permanently records the UTXO transfer as settled in `finalised_utxo_transfers`, causing any subsequent `fast_fin_transfer` call for the same ID to fail the `is_unified_transfer_finalised` check.

```rust
// utxo_fin_transfer_fast, NEAR-destination branch (line ~2529)
let amount = if fast_transfer.get_destination_chain() == ChainKind::Near {
    self.add_fin_utxo_transfer(&fast_transfer.transfer_id); // ADD THIS
    self.remove_fast_transfer(&fast_transfer.id());
    fast_transfer.amount
} else { ... }
```

### Proof of Concept

1. Register a trusted relayer account `relayer.near`.
2. Call `ft_on_transfer` on the bridge with a `FastFinTransfer` message: `transfer_id = {origin_chain: BTC, kind: Utxo("txid:0")}`, `recipient = OmniAddress::Near("victim.near")`, valid amount/fee.
   - Assert: `fast_transfers` contains the entry; tokens sent to `victim.near`.
3. As the UTXO connector, call `ft_on_transfer` with a `UtxoFinTransfer` message for the same `utxo_id`, same recipient.
   - Assert: `utxo_fin_transfer_fast` fires; `fast_transfers` entry removed; relayer reimbursed; `finalised_utxo_transfers` is **empty** for this ID.
4. Call `ft_on_transfer` again from `relayer.near` with the identical `FastFinTransfer` message from step 2.
   - Assert: `is_unified_transfer_finalised` returns `false`; `add_fast_transfer` succeeds; tokens sent to `victim.near` a **second time**.
5. Confirm token balance of `victim.near` is 2× the expected amount, demonstrating double-spend. [10](#0-9)

### Citations

**File:** near/omni-bridge/src/lib.rs (L778-780)
```rust
        if self.is_unified_transfer_finalised(&fast_fin_transfer_msg.transfer_id) {
            env::panic_str(BridgeError::TransferAlreadyFinalised.to_string().as_str());
        }
```

**File:** near/omni-bridge/src/lib.rs (L1475-1482)
```rust
    pub fn is_unified_transfer_finalised(&self, transfer_id: &UnifiedTransferId) -> bool {
        match transfer_id.kind {
            TransferIdKind::Nonce(nonce) => self.finalised_transfers.contains(&TransferId {
                origin_chain: transfer_id.origin_chain,
                origin_nonce: nonce,
            }),
            TransferIdKind::Utxo(_) => self.finalised_utxo_transfers.contains(transfer_id),
        }
```

**File:** near/omni-bridge/src/lib.rs (L2246-2265)
```rust
    fn add_fast_transfer(
        &mut self,
        fast_transfer: &FastTransfer,
        relayer: AccountId,
        storage_owner: AccountId,
    ) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.fast_transfers
                .insert(
                    &fast_transfer.id(),
                    &FastTransferStatusStorage::V0(FastTransferStatus {
                        relayer,
                        storage_owner,
                        finalised: false,
                    }),
                )
                .is_none(),
            BridgeError::FastTransferAlreadyPerformed.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L2483-2486)
```rust
        if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            // TODO: check how to deal with failed send_tokens
            return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
        }
```

**File:** near/omni-bridge/src/lib.rs (L2488-2490)
```rust
        let required_storage_balance =
            self.add_fin_utxo_transfer(&utxo_fin_transfer_msg.get_transfer_id(origin_chain));

```

**File:** near/omni-bridge/src/lib.rs (L2518-2561)
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

        env::log_str(
            &OmniBridgeEvent::UtxoTransferEvent {
                token_id: fast_transfer.token_id,
                amount,
                utxo_transfer_message: utxo_fin_transfer_msg,
                new_transfer_id: None,
            }
            .to_log_string(),
        );

        PromiseOrPromiseIndexOrValue::Value(U128(0))
    }
```

**File:** near/omni-bridge/src/lib.rs (L2877-2882)
```rust

```
