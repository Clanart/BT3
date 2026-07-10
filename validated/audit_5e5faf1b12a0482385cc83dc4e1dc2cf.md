### Title
Partial `ft_transfer_call` Acceptance Not Detected, Causing Permanent Loss of Bridged Funds - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

The NEAR bridge's `is_refund_required` helper only detects **full rejection** (zero tokens used) from `ft_transfer_call`, but silently treats **partial acceptance** as a complete success. When a recipient contract returns a non-zero but sub-total refund from `ft_on_transfer`, the bridge finalizes the transfer, pays the fee, and emits a success event — while the undelivered portion of tokens is permanently stranded in the bridge contract with no user-facing recovery path.

---

### Finding Description

In NEAR's NEP-141 standard, `ft_transfer_call` returns the **amount actually used** by the receiver (i.e., `original_amount − refund_amount`). A receiver contract's `ft_on_transfer` may legitimately return a partial refund (e.g., a liquidity pool that only has capacity for part of the deposit). In that case `ft_transfer_call` returns a value `X` where `0 < X < original_amount`.

The bridge's sole guard against failed token delivery is `is_refund_required`: [1](#0-0) 

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => {
                if let Ok(amount) = near_sdk::serde_json::from_slice::<U128>(&value) {
                    // Normal case: refund if the used token amount is zero
                    amount.0 == 0          // ← only catches full rejection
                } else {
                    false
                }
            }
            Err(_) => false,
        }
    } else {
        false
    }
}
```

The check `amount.0 == 0` is `true` only when **zero** tokens were accepted (full refund). When the receiver accepts, say, 60 out of 100 tokens and refunds 40, `ft_transfer_call` returns `60`, `amount.0 == 0` is `false`, and `is_refund_required` returns `false` — signalling success.

This function is the decision point for all three NEAR-side delivery callbacks:

**1. Regular fin-transfer to NEAR** — `fin_transfer_send_tokens_callback`: [2](#0-1) 

**2. Fast fin-transfer to NEAR** — `resolve_fast_transfer`: [1](#0-0) [3](#0-2) 

**3. UTXO fin-transfer to NEAR** — `resolve_utxo_fin_transfer`: [4](#0-3) 

In all three paths, when `is_refund_required` returns `false` (partial acceptance), the bridge:
- Marks the transfer as **finalized** (nonce consumed, cannot be replayed)
- Pays the fee to the fee recipient
- Emits a `FinTransferEvent` / `FastTransferEvent` success log

The partial refund tokens (`original_amount − X`) are returned by the token contract to the bridge contract's own account. There is no automated mechanism to re-deliver or return them to the user; the only recovery path is a privileged `transfer_token_as_dao` call.

The `msg` field that triggers `ft_transfer_call` is user-supplied via `InitTransferMsg`: [5](#0-4) 

Any user bridging to a NEAR contract recipient with a non-empty `msg` is exposed.

---

### Impact Explanation

- The source-chain transfer is already finalized (tokens burned/locked on origin chain).
- The NEAR-side transfer is marked finalized (nonce consumed).
- The recipient receives fewer tokens than the signed transfer message specifies.
- The undelivered portion is stranded in the bridge contract with no user-facing recovery.
- This constitutes **permanent, irrecoverable loss of bridged user funds** absent DAO intervention, breaking bridge collateralization accounting.

Matches allowed impact: **High — Balance/accounting corruption that breaks bridge collateralization or misdirects value** (and arguably Critical — permanent freezing of user funds in bridge flows).

---

### Likelihood Explanation

- The `msg` field is freely set by any bridge user; a non-empty `msg` is the normal path for DeFi integrations (e.g., bridging directly into a DEX, lending protocol, or yield vault on NEAR).
- NEAR contracts that implement `ft_on_transfer` and return partial refunds are standard and expected (e.g., AMMs with limited liquidity, vaults with deposit caps).
- No privileged access is required; any unprivileged user bridging to a contract recipient triggers this path.
- Likelihood: **Medium** (requires recipient to be a contract with partial-acceptance logic, which is common in DeFi).

---

### Recommendation

Replace the binary `amount.0 == 0` check with a comparison against the **expected full amount**. Pass the expected delivery amount into the callback and verify the returned used-amount equals it:

```rust
fn is_full_transfer_required(is_ft_transfer_call: bool, expected_amount: u128) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => {
                if let Ok(used) = near_sdk::serde_json::from_slice::<U128>(&value) {
                    used.0 < expected_amount  // partial OR full rejection → treat as failure
                } else {
                    false
                }
            }
            Err(_) => false,
        }
    } else {
        false
    }
}
```

Alternatively, follow the Uniswap v3 pattern referenced in the external report: allow users to opt in to partial fills via an explicit flag, and default to requiring full delivery.

---

### Proof of Concept

1. User on EVM initiates `initTransfer` of 100 USDC to NEAR, with `recipient = some_defi_contract.near` and `msg = '{"action":"deposit"}'`.
2. Relayer submits proof to NEAR `fin_transfer`; `fin_transfer_callback` creates a `TransferMessage` for 100 USDC (minus fee).
3. `process_fin_transfer_to_near` calls `send_tokens(token, some_defi_contract.near, 95, msg)`.
4. `ft_transfer_call` is issued; `some_defi_contract.near`'s `ft_on_transfer` accepts only 60 USDC (returns refund of 35).
5. Token contract refunds 35 USDC to the bridge; `ft_transfer_call` resolves with `used = 60`.
6. `fin_transfer_send_tokens_callback` calls `is_refund_required(true)` → reads `60` → `60 == 0` is `false` → returns `false`.
7. Bridge enters the **success branch**: pays fee, emits `FinTransferEvent`, marks nonce consumed.
8. Result: recipient received 60 USDC; 35 USDC are stranded in the bridge; user's 100 EVM USDC are permanently burned/locked; transfer cannot be retried. [1](#0-0) [6](#0-5) [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L540-553)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
```

**File:** near/omni-bridge/src/lib.rs (L896-912)
```rust
    pub fn resolve_fast_transfer(
        &mut self,
        token_id: &AccountId,
        fast_transfer_id: &FastTransferId,
        amount: U128,
        is_ft_transfer_call: bool,
    ) -> U128 {
        // Burn the tokens to ensure the locked tokens are not double-minted
        self.burn_tokens_if_needed(token_id.clone(), amount);

        if Self::is_refund_required(is_ft_transfer_call) {
            self.remove_fast_transfer(fast_transfer_id);
            amount
        } else {
            U128(0)
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1016-1044)
```rust
    pub fn resolve_utxo_fin_transfer(
        &mut self,
        token_id: AccountId,
        amount: U128,
        utxo_fin_transfer_msg: UtxoFinTransferMsg,
        origin_chain: ChainKind,
        storage_owner: &AccountId,
    ) -> U128 {
        let is_ft_transfer_call = !utxo_fin_transfer_msg.msg.is_empty();
        if Self::is_refund_required(is_ft_transfer_call) {
            self.remove_fin_utxo_transfer(
                &utxo_fin_transfer_msg.get_transfer_id(origin_chain),
                storage_owner,
            );
            amount
        } else {
            env::log_str(
                &OmniBridgeEvent::UtxoTransferEvent {
                    token_id,
                    amount,
                    utxo_transfer_message: utxo_fin_transfer_msg,
                    new_transfer_id: None,
                }
                .to_log_string(),
            );

            U128(0)
        }
    }
```

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
