### Title
Double-Finalization via Recipient-Controlled `ft_transfer_call` Rejection Removes Finalization Guard - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When a NEAR-destined `fin_transfer` routes through `ft_transfer_call` (triggered by a non-empty `msg` field) and the recipient contract rejects the tokens by returning the full amount from `ft_on_transfer`, the bridge removes the transfer ID from `finalised_transfers` inside `fin_transfer_send_tokens_callback`. This erasure of the finalization record allows the same cross-chain proof to be submitted a second time, enabling double-finalization and double-spending of bridged assets.

---

### Finding Description

The NEAR bridge contract's `process_fin_transfer_to_near` function marks a transfer as finalised by inserting its ID into `finalised_transfers` via `add_fin_transfer`, then dispatches tokens to the recipient via `send_tokens`. When `msg` is non-empty, `send_tokens` uses `ft_transfer_call`, and the result is handled in `fin_transfer_send_tokens_callback`. [1](#0-0) 

Inside `fin_transfer_send_tokens_callback`, if `is_refund_required` returns `true` (i.e., the recipient's `ft_on_transfer` returned the full amount, signalling rejection), the bridge executes a rollback path: [2](#0-1) 

This rollback:
1. Burns tokens (only for deployed tokens)
2. Reverts lock accounting via `revert_lock_actions`
3. **Removes the transfer ID from `finalised_transfers`** via `remove_fin_transfer` [3](#0-2) 

After this removal, `add_fin_transfer` will succeed again for the same transfer ID, because `finalised_transfers.insert` only panics on duplicate: [4](#0-3) 

The prover (light client or Wormhole VAA) does not track which proofs have been consumed — that is entirely the bridge's responsibility via `finalised_transfers`. Once the record is erased, the same proof bytes can be re-submitted to `fin_transfer`, pass proof verification again, and trigger a second token release.

`is_refund_required` is triggered when `ft_transfer_call` returns `U128(0)`: [5](#0-4) 

---

### Impact Explanation

**For non-deployed (native) tokens locked in the bridge:**
- Source chain: user locks 100 tokens → proof is generated.
- First `fin_transfer`: bridge calls `ft_transfer_call` on the recipient → recipient rejects → tokens stay in bridge → finalization record removed → locked counter restored.
- Second `fin_transfer` (same proof): bridge calls `ft_transfer_call` again → recipient accepts → 100 tokens released.

Result: 100 tokens released from the bridge with only one source-chain lock event. Bridge collateralization is broken.

**For deployed (bridged) tokens minted by the bridge:**
- First `fin_transfer`: bridge mints 100 tokens → recipient rejects → tokens returned to bridge → bridge burns them → finalization record removed.
- Second `fin_transfer` (same proof): bridge mints 100 tokens again → recipient accepts.

Result: 100 tokens minted twice from a single source-chain lock. Unbacked supply is created.

Both outcomes match the **High** impact class: "Cross-chain replay, double-finalization, nonce reuse, or duplicate settlement that enables double-spend or unbacked supply."

---

### Likelihood Explanation

The attacker controls the recipient NEAR contract because they specified it in the source-chain `InitTransfer` event. The `msg` field is also attacker-controlled (specified at transfer initiation). The attack requires:

1. Attacker deploys a NEAR contract that rejects `ft_on_transfer` on the first call and accepts on the second.
2. Attacker initiates a transfer from EVM/Solana/StarkNet to that NEAR contract with a non-empty `msg`.
3. Attacker (or any relayer) submits `fin_transfer` with the valid proof → first call rejected → finalization record erased.
4. Attacker submits the same proof again → second call accepted → tokens double-released.

This is a fully unprivileged, self-contained attack requiring no trusted role, no key compromise, and no colluding MPC signers. The attacker only needs to control a NEAR contract address and initiate a standard bridge transfer.

---

### Recommendation

Do **not** remove the transfer ID from `finalised_transfers` on refund. The finalization record must be permanent once inserted, regardless of whether the downstream `ft_transfer_call` succeeds or fails. If the recipient rejects the tokens, the bridge should either:

- Keep the transfer marked as finalised and allow the relayer to retry delivery via a separate, idempotent retry mechanism that does not re-verify the proof, or
- Redirect the tokens to a fallback address (e.g., the original sender or a claimable escrow) without erasing the finalization guard.

The `remove_fin_transfer` call inside the refund branch of `fin_transfer_send_tokens_callback` is the root cause and must be removed: [6](#0-5) 

---

### Proof of Concept

1. Attacker deploys NEAR contract `attacker.near` with `ft_on_transfer` that returns `amount` (full rejection) on the first call and `0` (acceptance) on the second call.
2. On EVM, attacker calls `initTransfer(token, 100, 0, 0, "near:attacker.near", "trigger_ft_transfer_call")`. This emits an `InitTransfer` event with `msg = "trigger_ft_transfer_call"`.
3. Relayer submits `fin_transfer` on NEAR with the EVM proof. `process_fin_transfer_to_near` runs:
   - `add_fin_transfer` inserts `(Eth, nonce=N)` into `finalised_transfers`.
   - `unlock_tokens_if_needed` decrements locked ETH-side tokens by 100.
   - `send_tokens` calls `ft_transfer_call(attacker.near, 100, "trigger_ft_transfer_call")`.
   - `attacker.near::ft_on_transfer` returns `U128(100)` (rejection).
4. `fin_transfer_send_tokens_callback` fires with `is_refund_required = true`:
   - `burn_tokens_if_needed` (no-op for native token).
   - `revert_lock_actions` re-locks 100 tokens.
   - `remove_fin_transfer` **erases** `(Eth, nonce=N)` from `finalised_transfers`.
5. Attacker submits the same EVM proof again via `fin_transfer`. `add_fin_transfer` succeeds (record was erased). `ft_transfer_call` fires again; this time `attacker.near::ft_on_transfer` returns `U128(0)` (acceptance).
6. 100 tokens are released to `attacker.near` a second time from a single source-chain lock. [7](#0-6) [8](#0-7)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1700-1718)
```rust
        let token = self.get_token_id(&transfer_message.token);

        if Self::is_refund_required(is_ft_transfer_call) {
            self.burn_tokens_if_needed(
                token.clone(),
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
            );

            self.revert_lock_actions(&lock_actions);

            self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);

            env::log_str(
                &OmniBridgeEvent::FailedFinTransferEvent { transfer_message }.to_log_string(),
            );
```

**File:** near/omni-bridge/src/lib.rs (L1784-1804)
```rust
    fn is_refund_required(is_ft_transfer_call: bool) -> bool {
        if is_ft_transfer_call {
            match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
                Ok(value) => {
                    if let Ok(amount) = near_sdk::serde_json::from_slice::<U128>(&value) {
                        // Normal case: refund if the used token amount is zero
                        // The amount can be zero if the `ft_on_transfer` in the receiver contract returns an amount instead of `0`, or if it panics.
                        amount.0 == 0
                    } else {
                        // Unexpected case: don't refund
                        false
                    }
                }
                // Unexpected case: don't refund
                Err(_) => false,
            }
        } else {
            // Not ft_transfer_call: don't refund
            false
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1875-1875)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
```

**File:** near/omni-bridge/src/lib.rs (L1957-1977)
```rust
        self.send_tokens(
            token.clone(),
            recipient,
            U128(
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            ),
            &msg,
        )
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(SEND_TOKENS_CALLBACK_GAS)
                .fin_transfer_send_tokens_callback(
                    transfer_message,
                    &fee_recipient,
                    !msg.is_empty(),
                    predecessor_account_id,
                    lock_actions,
                ),
        )
```

**File:** near/omni-bridge/src/lib.rs (L2226-2234)
```rust
    fn add_fin_transfer(&mut self, transfer_id: &TransferId) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.finalised_transfers.insert(transfer_id),
            BridgeError::TransferAlreadyFinalised.as_ref()
        );
        env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
    }
```

**File:** near/omni-bridge/src/lib.rs (L2322-2333)
```rust
    fn remove_fin_transfer(&mut self, transfer_id: &TransferId, storage_owner: &AccountId) {
        let storage_usage = env::storage_usage();
        self.finalised_transfers.remove(transfer_id);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(storage_owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(storage_owner, &storage);
        }
    }
```
