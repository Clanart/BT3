Let me trace the exact code path described in this question.

I have all the code needed to trace the exact execution path. Let me analyze it carefully.

## Tracing the Execution Path

**Step 1: `fin_transfer` → `process_fin_transfer_to_near`** [1](#0-0) 

`process_fin_transfer_to_near` calls `add_fin_transfer` first, inserting `TransferId{chain,N}` into `finalised_transfers`: [2](#0-1) 

**Step 2: `add_fin_transfer` guard** [3](#0-2) 

The `require!` here panics if the key already exists — this is the only replay guard in the bridge contract.

**Step 3: `send_tokens` with non-empty `msg` → `mint(recipient, amount, Some(msg))`**

For deployed tokens with a non-empty `msg`, `send_tokens` calls `mint` with the message, which internally calls `ft_on_transfer` on the recipient: [4](#0-3) 

**Step 4: `fin_transfer_send_tokens_callback` — the failure path** [5](#0-4) 

When `is_refund_required` returns `true`, the callback:
1. Calls `burn_tokens_if_needed` (detached — result ignored)
2. Calls `remove_fin_transfer`, **deleting `TransferId{chain,N}` from `finalised_transfers`**

**Step 5: `is_refund_required` logic** [6](#0-5) 

Returns `true` when `amount.0 == 0` — i.e., when `ft_transfer_call`/`mint` returns 0, meaning the recipient rejected all tokens (returned the full amount from `ft_on_transfer`).

**Step 6: `remove_fin_transfer` removes the guard** [7](#0-6) 

After this, `finalised_transfers` no longer contains `TransferId{chain,N}`. The bridge's only replay protection is gone.

**Step 7: Re-submission**

The relayer re-submits the identical `FinTransferArgs`. `verify_proof` is called again (the prover is a separate stateless verifier — the bridge contract itself has no proof-hash deduplication). `fin_transfer_callback` → `process_fin_transfer_to_near` → `add_fin_transfer` **succeeds** (key was removed). `send_tokens` mints tokens again.

**Step 8: `burn_tokens_if_needed` does not compensate** [8](#0-7) 

The burn is `.detach()`-ed. For a deployed (bridged) token, the tokens were already burned by the token contract during the NEP-141 refund flow. The bridge holds no balance to burn. The detached call fails silently, leaving the net effect as: tokens minted once, burned once (by token contract), then minted again on re-submission — net double-mint.

---

### Title
Double-Finalization via `remove_fin_transfer` in Failure Path Enables Double-Mint of Bridged Tokens — (File: `near/omni-bridge/src/lib.rs`)

### Summary
`fin_transfer_send_tokens_callback` unconditionally removes `TransferId{chain,N}` from `finalised_transfers` when the recipient contract rejects all tokens (`is_refund_required == true`). This erases the bridge's sole replay-protection guard, allowing a registered relayer to re-submit the identical `FinTransferArgs` and finalize the same origin event a second time, minting bridged tokens twice against a single locked amount on the origin chain.

### Finding Description

The bridge's replay protection for `fin_transfer` is the `finalised_transfers: LookupSet<TransferId>` set. `add_fin_transfer` inserts the key and panics if it already exists. This is the **only** guard in the bridge contract against double-finalization. [3](#0-2) 

In the failure path of `fin_transfer_send_tokens_callback`, when `is_refund_required` is `true` (recipient's `ft_on_transfer` returned the full amount, causing `ft_transfer_call`/`mint` to return 0), the bridge calls `remove_fin_transfer`: [9](#0-8) [7](#0-6) 

After removal, `finalised_transfers` no longer contains `TransferId{chain,N}`. A subsequent call to `fin_transfer` with the same `FinTransferArgs` passes `add_fin_transfer` again, re-minting the full token amount.

The `burn_tokens_if_needed` call in the failure path is detached and fails silently for deployed tokens (the token contract already burned the refunded tokens; the bridge holds no balance): [8](#0-7) 

The net accounting after one failed + one successful finalization: origin chain locked `X` tokens; NEAR minted `2X` tokens.

### Impact Explanation

For any bridged (deployed) token whose recipient contract can be made to reject tokens on the first finalization attempt, the same origin-chain event can be finalized twice. Each finalization mints `amount_without_fee` tokens. Total minted supply exceeds total locked supply on the origin chain, breaking bridge collateralization. This is an unbacked token supply — a direct theft-equivalent for holders of the bridged token.

Impact: **High — cross-chain double-finalization enabling unbacked supply / double-mint**.

### Likelihood Explanation

- The attacker must be a registered relayer (permissioned but not a privileged admin role; relayers are expected to submit proofs, not be fully trusted with bridge funds).
- The attacker deploys a recipient contract whose `ft_on_transfer` returns the full amount on the first call and 0 on the second call (or simply waits for the recipient to be ready).
- The same `FinTransferArgs` (same proof bytes) is submitted twice. The prover contract is a separate stateless verifier; the bridge contract itself has no proof-hash deduplication — `finalised_transfers` is the only guard, and it has been cleared.
- No special cryptographic material, key leakage, or MPC collusion is required.

### Recommendation

Do **not** remove `TransferId` from `finalised_transfers` in the failure path. Once a transfer ID has been finalized (even if token delivery failed), it must remain in `finalised_transfers` permanently. If token delivery fails, handle recovery through a separate mechanism (e.g., a claimable refund on the origin chain, or a retry that does not re-enter the finalization guard). The fix is a one-line deletion of the `remove_fin_transfer` call inside the `is_refund_required` branch of `fin_transfer_send_tokens_callback`.

### Proof of Concept

1. Deploy a recipient contract `R` on NEAR whose `ft_on_transfer` returns the full `amount` on the first call and `0` on the second call.
2. Initiate a transfer of bridged token `T` (deployed token) from the origin chain to `R` on NEAR, with a non-empty `msg`.
3. As a registered relayer, call `fin_transfer(FinTransferArgs{ chain_kind, storage_deposit_actions, prover_args })`.
4. Observe: `add_fin_transfer` inserts `TransferId{chain,N}`; `mint(R, amount, Some(msg))` is called; `R.ft_on_transfer` returns `amount`; token contract burns the minted tokens; `fin_transfer_send_tokens_callback` fires with `is_refund_required = true`; `remove_fin_transfer` deletes `TransferId{chain,N}`.
5. Call `fin_transfer` again with the identical `FinTransferArgs`.
6. Observe: `add_fin_transfer` succeeds (key absent); `mint(R, amount, Some(msg))` is called again; `R.ft_on_transfer` returns `0`; tokens remain with `R`.
7. Assert: `R` holds `amount` tokens; total minted supply of `T` on NEAR exceeds the amount locked on the origin chain by `amount`. [10](#0-9)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1692-1747)
```rust
    pub fn fin_transfer_send_tokens_callback(
        &mut self,
        #[serializer(borsh)] transfer_message: TransferMessage,
        #[serializer(borsh)] fee_recipient: &AccountId,
        #[serializer(borsh)] is_ft_transfer_call: bool,
        #[serializer(borsh)] storage_owner: &AccountId,
        #[serializer(borsh)] lock_actions: Vec<LockAction>,
    ) {
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
        } else {
            // Send fee to the fee recipient
            if transfer_message.fee.fee.0 > 0 {
                if self.is_deployed_token(&token) {
                    ext_token::ext(token)
                        .with_static_gas(MINT_TOKEN_GAS)
                        .mint(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                } else {
                    ext_token::ext(token)
                        .with_attached_deposit(ONE_YOCTO)
                        .with_static_gas(FT_TRANSFER_GAS)
                        .ft_transfer(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                }
            }

            if transfer_message.fee.native_fee.0 > 0 {
                let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

                ext_token::ext(native_token_id)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }

            env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1784-1803)
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

**File:** near/omni-bridge/src/lib.rs (L1867-1878)
```rust
    #[allow(clippy::too_many_lines, clippy::ptr_arg)]
    fn process_fin_transfer_to_near(
        &mut self,
        recipient: AccountId,
        predecessor_account_id: &AccountId,
        transfer_message: TransferMessage,
        storage_deposit_actions: &Vec<StorageDepositAction>,
    ) -> Promise {
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());

        let token = self.get_token_id(&transfer_message.token);
        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
```

**File:** near/omni-bridge/src/lib.rs (L2094-2101)
```rust
            ext_token::ext(token)
                .with_attached_deposit(deposit)
                .with_static_gas(MINT_TOKEN_GAS.saturating_add(ft_transfer_call_gas))
                .mint(
                    recipient,
                    amount,
                    (!msg.is_empty()).then(|| msg.to_string()),
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
