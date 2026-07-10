### Title
Stale Fee State in `sign_transfer_callback` Causes Fee Accounting Corruption and Loss of Native Fee Payments - (File: near/omni-bridge/src/lib.rs)

### Summary

`sign_transfer_callback` uses the fee value captured at `sign_transfer` invocation time — not the current on-chain fee — to decide whether to remove the pending transfer message. Because NEAR's MPC signing takes ~30 seconds and `update_transfer_fee` can be called by any party during that window, the callback acts on stale state, misdirecting fees and permanently losing native fee payments.

### Finding Description

`sign_transfer` reads the transfer message, captures the fee, and dispatches an async MPC signing request: [1](#0-0) 

The captured `fee` is passed as a serialized argument into the callback: [2](#0-1) 

`sign_transfer_callback` then uses that stale `fee` — not the live storage value — to decide whether to remove the transfer message: [3](#0-2) 

Between the `sign_transfer` call and the callback, `update_transfer_fee` can be called by the sender (or by anyone for native-fee-only updates): [4](#0-3) 

The fee update stores the new fee in the transfer message and collects the `diff_native_fee` as an attached deposit: [5](#0-4) 

When the callback fires, it sees the old fee, not the updated one.

### Impact Explanation

**Scenario A — fee was zero at `sign_transfer` time, updated to non-zero before callback:**

1. `sign_transfer_callback` sees `fee.is_zero() == true` → removes the transfer message.
2. The emitted `SignTransferEvent` carries a signature over the old payload (fee = 0).
3. The relayer finalises on the destination chain with fee = 0; the recipient receives the full amount.
4. The native fee the user attached to `update_transfer_fee` is permanently lost: it was credited to the contract's balance but the transfer message (which would have triggered `send_fee_internal`) was already deleted.

**Scenario B — fee was non-zero at `sign_transfer` time, updated to a higher value before callback:**

1. `sign_transfer_callback` sees `fee.is_zero() == false` → does NOT remove the transfer message.
2. The signature covers the old, lower fee.
3. The relayer finalises on the destination chain with the old fee; the fee recipient receives less than the updated amount.
4. The transfer message remains in `pending_transfers` with the higher fee, but the destination nonce is already consumed (`completedTransfers[nonce] = true` on EVM), so the higher-fee signature can never be used. The fee difference is permanently misdirected. [6](#0-5) 

### Likelihood Explanation

- NEAR's MPC signing takes approximately 30 seconds per the project documentation, creating a wide race window.
- `update_transfer_fee` for native-fee-only changes is callable by any account (not just the sender), widening the attack surface.
- The relayer submitting `sign_transfer` and the user submitting `update_transfer_fee` are independent actors whose transactions can interleave within the same block or across consecutive blocks.
- No locking or mutex prevents `update_transfer_fee` from executing while a signing promise is in flight.

### Recommendation

In `sign_transfer_callback`, re-read the current fee from storage instead of relying on the fee serialised at call time:

```rust
pub fn sign_transfer_callback(
    &mut self,
    #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
    #[serializer(borsh)] message_payload: TransferMessagePayload,
    #[serializer(borsh)] _fee_at_sign_time: &Fee,   // kept for ABI compat only
) {
    if let Ok(signature) = call_result {
        // Re-read the live fee from storage
        let current_fee = self
            .get_transfer_message(message_payload.transfer_id)
            .fee;
        if current_fee.is_zero() {
            self.remove_transfer_message(message_payload.transfer_id);
        }
        env::log_str(...);
    }
}
```

Additionally, consider disallowing `update_transfer_fee` while a signing promise is in flight (e.g., by recording an in-flight flag per `transfer_id`), or restricting native-fee updates to the transfer sender only.

### Proof of Concept

1. Alice initiates a transfer with `fee = 0` (no token fee, no native fee).
2. Relayer Bob calls `sign_transfer(transfer_id, fee_recipient, None)`. The contract reads `fee = 0`, builds `TransferMessagePayload`, and dispatches the MPC signing call. The captured `fee = 0` is serialised into the callback arguments.
3. ~5 seconds later, Alice calls `update_transfer_fee` with `fee.native_fee = 1_000_000_000_000_000_000_000` (1 mNEAR), attaching 1 mNEAR as deposit. The transfer message in storage is updated to `native_fee = 1 mNEAR`.
4. ~25 seconds later, the MPC signing completes. `sign_transfer_callback` fires with the serialised `fee = 0`.
5. Because `fee.is_zero()` is `true`, `remove_transfer_message` is called — the transfer message (now carrying `native_fee = 1 mNEAR`) is deleted.
6. `SignTransferEvent` is emitted with a valid signature over the zero-fee payload.
7. Bob submits `finTransfer` on the EVM destination chain using this signature. The recipient receives the full transfer amount with no fee deducted.
8. Alice's 1 mNEAR native fee payment is permanently lost: it was absorbed into the contract's balance when `update_transfer_fee` was called, but `send_fee_internal` is never invoked because the transfer message was already removed. [3](#0-2) [4](#0-3) [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L386-436)
```rust
    #[payable]
    #[pause]
    pub fn update_transfer_fee(&mut self, transfer_id: TransferId, fee: UpdateFee) {
        match fee {
            UpdateFee::Fee(fee) => {
                let mut transfer = self.get_transfer_message_storage(transfer_id);

                require!(
                    transfer.message.origin_transfer_id.is_none(),
                    BridgeError::UpdateFeeNotAllowedForTransfer.as_ref()
                );

                let current_fee = transfer.message.fee;
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
                );

                require!(
                    fee.fee == current_fee.fee
                        || OmniAddress::Near(env::predecessor_account_id())
                            == transfer.message.sender,
                    BridgeError::SenderCanUpdateTokenFeeOnly.as_ref()
                );

                let diff_native_fee = fee
                    .native_fee
                    .0
                    .checked_sub(current_fee.native_fee.0)
                    .near_expect(BridgeError::LowerFee);

                require!(
                    NearToken::from_yoctonear(diff_native_fee) == env::attached_deposit(),
                    BridgeError::InvalidAttachedDeposit.as_ref()
                );

                transfer.message.fee = fee;
                self.insert_raw_transfer(transfer.message.clone(), transfer.owner);

                env::log_str(
                    &OmniBridgeEvent::UpdateFeeEvent {
                        transfer_message: transfer.message,
                    }
                    .to_log_string(),
                );
            }
            UpdateFee::Proof(_) => {
                env::panic_str(BridgeError::UnsupportedFeeUpdateProof.to_string().as_str())
            }
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L447-521)
```rust
    pub fn sign_transfer(
        &mut self,
        transfer_id: TransferId,
        fee_recipient: Option<AccountId>,
        fee: &Option<Fee>,
    ) -> Promise {
        let transfer_message = self.get_transfer_message(transfer_id);

        if let Some(fee) = &fee {
            require!(
                &transfer_message.fee == fee,
                BridgeError::InvalidFee.as_ref()
            );
        }

        let token_address = self
            .get_token_address(
                transfer_message.get_destination_chain(),
                self.get_token_id(&transfer_message.token),
            )
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });

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

        let message = DestinationChainMsg::from_json(&transfer_message.msg)
            .and_then(|s| s.destination_msg())
            .unwrap_or_default();

        let transfer_payload = TransferMessagePayload {
            prefix: PayloadType::TransferMessage,
            destination_nonce: transfer_message.destination_nonce,
            transfer_id,
            token_address,
            amount: U128(amount_to_transfer),
            recipient: transfer_message.recipient,
            fee_recipient,
            message,
        };

        let payload = near_sdk::env::keccak256_array(
            transfer_payload
                .encode_hashable()
                .near_expect(BridgeError::Borsh),
        );

        ext_signer::ext(self.mpc_signer.clone())
            .with_static_gas(MPC_SIGNING_GAS)
            .with_attached_deposit(env::attached_deposit())
            .sign(SignRequest {
                payload,
                path: SIGN_PATH.to_owned(),
                key_version: 0,
            })
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(SIGN_TRANSFER_CALLBACK_GAS)
                    .sign_transfer_callback(transfer_payload, &transfer_message.fee),
            )
    }
```

**File:** near/omni-bridge/src/lib.rs (L649-668)
```rust
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-287)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;
```
