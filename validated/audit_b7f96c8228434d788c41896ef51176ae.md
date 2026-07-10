### Title
Finalization Record Removed on `ft_transfer_call` Rejection Enables Cross-Chain Replay / Double-Finalization — (`File: near/omni-bridge/src/lib.rs`)

### Summary

In `process_fin_transfer_to_near`, when a `ft_transfer_call` to the recipient is rejected (the receiver's `ft_on_transfer` returns 0), the callback `fin_transfer_send_tokens_callback` calls `remove_fin_transfer`, which **deletes the replay-protection entry** from `finalised_transfers`. Because the prover contract does not track consumed proofs, the same EVM `InitTransfer` proof can be submitted a second time, causing a second token release to the same recipient — a double-finalization.

### Finding Description

The NEAR bridge contract uses `finalised_transfers: LookupSet<TransferId>` as its sole replay guard for incoming cross-chain transfers. When `fin_transfer` is called, `process_fin_transfer_to_near` inserts the transfer ID into this set via `add_fin_transfer` before sending tokens. [1](#0-0) 

If the token delivery uses `ft_transfer_call` (triggered when the transfer's `msg` field is non-empty) and the recipient contract rejects the tokens (returns `0` from `ft_on_transfer`), the callback `fin_transfer_send_tokens_callback` detects this via `is_refund_required` and calls `remove_fin_transfer`: [2](#0-1) 

`remove_fin_transfer` unconditionally deletes the entry from `finalised_transfers`: [3](#0-2) 

After this deletion, `finalised_transfers` no longer contains the transfer ID. The prover contract (called by `verify_proof`) verifies only that the EVM event occurred on-chain; it does not maintain its own consumed-proof registry. Therefore the identical proof can be submitted again via `fin_transfer`, `add_fin_transfer` will succeed (the set no longer contains the ID), and a second token release is executed.

For **native (locked) tokens**: `burn_tokens_if_needed` is a no-op for non-deployed tokens, so the returned tokens remain in the bridge. The second finalization transfers them to the recipient again — a direct double-spend. [4](#0-3) 

For **deployed (bridged) tokens**: the returned tokens are burned after the first rejection, but the second finalization mints a fresh amount — unbacked supply creation.

### Impact Explanation

An attacker who controls the NEAR recipient contract can:
1. Receive tokens via `ft_transfer_call`.
2. Have their contract reject the transfer (return `0`).
3. Observe that `remove_fin_transfer` fires, clearing the replay guard.
4. Re-submit the identical EVM proof.
5. Accept the second delivery.

For native tokens this is a direct double-spend of bridge-locked funds. For bridged tokens this creates unbacked supply. Both outcomes match **Critical** impact (unauthorized release / unauthorized mint of bridged assets).

### Likelihood Explanation

The attacker needs only:
- An EVM wallet to call `initTransfer` with a non-empty `msg` (fully permissionless).
- A NEAR contract that returns `0` from `ft_on_transfer` (trivial to deploy).
- The ability to submit the same proof twice (any relayer or the attacker themselves).

No privileged role, leaked key, or colluding MPC signer is required. The entire attack path is reachable by an unprivileged bridge user.

### Recommendation

Do **not** remove the finalization record on `ft_transfer_call` rejection. The `finalised_transfers` entry must be permanent once inserted. If the delivery fails, the bridge should either:
1. Keep the finalization record and allow the relayer to retry delivery via a separate, idempotent retry path that does not re-verify the proof; or
2. Refund the user on the origin chain through a separate signed message, without ever clearing the finalization record.

Remove the `self.remove_fin_transfer(...)` call from the refund branch of `fin_transfer_send_tokens_callback`: [5](#0-4) 

### Proof of Concept

1. Attacker deploys `AttackerReceiver.near` — a NEAR contract whose `ft_on_transfer` always returns the full `amount` (rejecting all tokens).
2. Attacker calls `initTransfer` on the EVM bridge with:
   - `recipient = "AttackerReceiver.near"`
   - `msg = "x"` (non-empty, forces `ft_transfer_call`)
   - `amount = 1000 USDC`
3. Relayer (or attacker) submits the EVM proof to NEAR `fin_transfer`.
4. `process_fin_transfer_to_near` runs:
   - `add_fin_transfer` inserts `(Eth, nonce=N)` into `finalised_transfers`. ✓
   - `send_tokens` calls `ft_transfer_call` to `AttackerReceiver.near`.
   - `AttackerReceiver.near.ft_on_transfer` returns `1000` (full amount rejected).
   - `fin_transfer_send_tokens_callback` sees `is_refund_required = true`.
   - `burn_tokens_if_needed` is a no-op (native USDC, not a deployed token).
   - `remove_fin_transfer` **deletes** `(Eth, nonce=N)` from `finalised_transfers`. ✗
5. Attacker submits the **same EVM proof** again to `fin_transfer`.
6. `add_fin_transfer` succeeds (set is empty for this ID).
7. `send_tokens` calls `ft_transfer_call` again.
8. This time `AttackerReceiver.near.ft_on_transfer` returns `0` (accepts tokens).
9. Attacker receives 1000 USDC a second time. Bridge has paid out 2000 USDC for a single 1000 USDC EVM lock. [6](#0-5) [7](#0-6) [3](#0-2)

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

**File:** near/omni-bridge/src/lib.rs (L1806-1813)
```rust
    fn burn_tokens_if_needed(&self, token: AccountId, amount: U128) {
        if self.is_deployed_token(&token) {
            ext_token::ext(token)
                .with_static_gas(BURN_TOKEN_GAS)
                .burn(amount)
                .detach();
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1875-1875)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
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
