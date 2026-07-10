### Title
Dust Transfer Permanently Freezes Funds When `normalize_amount` Returns Zero in `sign_transfer` — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When a cross-chain transfer targeting a non-NEAR destination is finalized on NEAR via `fin_transfer_callback`, the transfer is stored in `pending_transfers` and locked-token accounting is updated without verifying that the stored amount will produce a non-zero value after decimal normalization to the destination chain's precision. The subsequent `sign_transfer` call enforces `require!(amount_to_transfer > 0)` and permanently reverts if normalization rounds down to zero. Because `sign_transfer` is the only path to produce an MPC signature (required for `claim_fee` to remove the transfer), the transfer is irrecoverably stuck and the user's funds are permanently frozen.

---

### Finding Description

**Step 1 — Transfer is stored without a normalized-amount check.**

In `fin_transfer_callback`, the amount is denormalized from the source chain's representation to NEAR's native decimal precision and stored directly:

```rust
// near/omni-bridge/src/lib.rs ~L715-L732
let decimals = self
    .token_decimals
    .get(&init_transfer.token)
    .near_expect(BridgeError::TokenDecimalsNotFound);

let transfer_message = TransferMessage {
    ...
    amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
    ...
};
```

No check is performed to verify that `normalize_amount` applied to this stored amount for the *destination* chain will produce a non-zero result.

For a non-NEAR recipient, `process_fin_transfer_to_other_chain` is called, which:
- Decrements the origin chain's `locked_tokens`
- Increments the destination chain's `locked_tokens`
- Stores the transfer in `pending_transfers`

All of these state changes are committed and cannot be rolled back.

**Step 2 — `sign_transfer` permanently reverts.**

```rust
// near/omni-bridge/src/lib.rs ~L471-L485
let decimals = self
    .token_decimals
    .get(&token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);
let amount_to_transfer = Self::normalize_amount(
    transfer_message
        .amount_without_fee()
        .near_expect(BridgeError::InvalidFee),
    decimals,
);

require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
```

If the destination chain's token has fewer decimal places than NEAR's internal representation (e.g., NEAR uses 24 decimals internally, destination uses 6), a small stored amount can normalize to zero via integer division. Every call to `sign_transfer` for this transfer will panic with `ERR_INVALID_AMOUNT_TO_TRANSFER`.

**Step 3 — No recovery path exists.**

`remove_transfer_message` is only called from:
- `sign_transfer_callback` — unreachable because `sign_transfer` panics before the MPC call
- `claim_fee_callback` — requires a proof of finalization from the destination chain, which is impossible without a valid MPC signature

There is no DAO or admin function to forcibly remove a stuck pending transfer. The transfer entry and the destination chain's `locked_tokens` balance are permanently corrupted.

---

### Impact Explanation

**Permanent freezing of user funds** — matches the allowed impact class *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

- The user's tokens are held by the bridge contract but can never be claimed on the destination chain (no signature) and can never be returned to the origin chain (no refund path).
- The destination chain's `locked_tokens` accounting is permanently inflated, corrupting bridge collateralization.

---

### Likelihood Explanation

- Tokens with different decimal representations on different chains are common (e.g., a token with 18 decimals on Ethereum bridged to a chain where it is represented with 6 decimals).
- Any user can initiate a transfer with a dust amount on the source chain. Even if the source chain enforces a minimum, the decimal conversion can still produce zero on the destination.
- No special privilege is required; any bridge user can trigger this condition.

---

### Recommendation

Add a normalization check in `fin_transfer_callback` (or in `process_fin_transfer_to_other_chain`) before committing state, to reject transfers whose amount normalizes to zero for the destination chain:

```rust
// After computing transfer_message.amount, before storing:
let dest_token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    self.get_token_id(&transfer_message.token),
).near_expect(BridgeError::FailedToGetTokenAddress);
let dest_decimals = self.token_decimals
    .get(&dest_token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);
let normalized = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    dest_decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

This mirrors the check already present in `sign_transfer` and prevents the state from being committed for transfers that can never be finalized.

---

### Proof of Concept

1. A token `T` is registered with `origin_decimals = 18` on Ethereum and `decimals = 24` on NEAR (standard NEAR precision).
2. The destination chain (e.g., Solana) represents `T` with 6 decimals.
3. A user on Ethereum calls `initTransfer` with `amount = 1` (1 wei of `T`).
4. The relayer calls `fin_transfer` on NEAR with a valid proof.
5. `fin_transfer_callback` computes `denormalize_amount(1, decimals)` = `1 × 10^(24−18)` = `10^6` and stores this in `pending_transfers`. `process_fin_transfer_to_other_chain` decrements Ethereum's `locked_tokens` and increments Solana's `locked_tokens`.
6. The relayer calls `sign_transfer`. `normalize_amount(10^6, dest_decimals)` = `10^6 / 10^(24−6)` = `10^6 / 10^18` = `0`. The `require!(amount_to_transfer > 0)` check panics.
7. Every subsequent call to `sign_transfer` for this transfer ID panics identically. The transfer is permanently stuck. The user's 1 wei is irrecoverably locked in the bridge. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L471-485)
```rust
        let decimals = self
            .token_decimals
            .get(&token_address)
            .near_expect(BridgeError::TokenDecimalsNotFound);
        let amount_to_transfer = Self::normalize_amount(
            transfer_message
                .amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );

        require!(
            amount_to_transfer > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L648-668)
```rust
    #[private]
    pub fn sign_transfer_callback(
        &mut self,
        #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
        #[serializer(borsh)] message_payload: TransferMessagePayload,
        #[serializer(borsh)] fee: &Fee,
    ) {
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }

            env::log_str(
                &OmniBridgeEvent::SignTransferEvent {
                    signature,
                    message_payload,
                }
                .to_log_string(),
            );
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L715-732)
```rust
        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };
```

**File:** near/omni-bridge/src/lib.rs (L1980-2054)
```rust
    fn process_fin_transfer_to_other_chain(
        &mut self,
        predecessor_account_id: AccountId,
        transfer_message: TransferMessage,
    ) {
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
        let token = self.get_token_id(&transfer_message.token);

        if transfer_message.recipient.is_utxo_chain() {
            let btc_account_id =
                self.get_utxo_chain_token(transfer_message.get_destination_chain());
            require!(
                token == btc_account_id,
                BridgeError::NativeTokenRequiredForChain.as_ref()
            );
        }

        self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        );
        self.lock_tokens_if_needed(
            transfer_message.get_destination_chain(),
            &token,
            transfer_message.fee.fee.into(),
        );

        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let recipient = if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            require!(
                !status.finalised,
                BridgeError::FastTransferAlreadyFinalised.as_ref()
            );
            Some(status.relayer)
        } else {
            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token,
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            );

            None
        };

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
            required_balance = self
                .add_transfer_message(transfer_message.clone(), predecessor_account_id.clone())
                .saturating_add(required_balance);
        }

        self.update_storage_balance(
            predecessor_account_id,
            required_balance,
            env::attached_deposit(),
        );

        env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
    }
```

**File:** near/omni-bridge/src/lib.rs (L2194-2211)
```rust
    fn remove_transfer_message(&mut self, transfer_id: TransferId) -> TransferMessage {
        let storage_usage = env::storage_usage();
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(&transfer.owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(&transfer.owner, &storage);
        }

        transfer.message
    }
```
