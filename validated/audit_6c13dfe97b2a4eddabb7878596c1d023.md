### Title
Permanent Freezing of Funds When Destination Recipient Becomes Blacklisted for In-Flight NEAR → EVM Transfers — (File: `near/omni-bridge/src/lib.rs`, `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

When a user initiates a transfer from NEAR to an EVM chain, tokens are immediately locked or burned on NEAR with a fixed `recipient` address. If that recipient's EVM address is subsequently blacklisted by a token issuer (e.g., Circle for USDC) while the transfer is pending, every attempt to call `finTransfer` on the EVM side will revert. Because the NEAR bridge contract provides no mechanism to update the recipient or cancel the pending transfer, the locked/burned tokens are irrecoverable.

---

### Finding Description

**Root cause — NEAR side (`near/omni-bridge/src/lib.rs`)**

`init_transfer` locks or burns the user's tokens on NEAR and stores the transfer in `pending_transfers` with a fixed `recipient` field: [1](#0-0) 

The only public mutation function for a pending transfer is `update_transfer_fee`, which explicitly rejects any change other than a fee increase: [2](#0-1) 

`sign_transfer` then reads the stored `recipient` verbatim and embeds it into the MPC-signed `TransferMessagePayload`: [3](#0-2) 

There is no `cancel_transfer`, `update_recipient`, or refund path anywhere in the contract. Once tokens are locked/burned, the only exit is a successful `finTransfer` on the destination chain with the original recipient.

**Failure point — EVM side (`evm/src/omni-bridge/contracts/OmniBridge.sol`)**

`finTransfer` marks the destination nonce as used *before* the token transfer, but because the EVM transaction is atomic, a revert in the transfer step also reverts the nonce marking: [4](#0-3) 

For tokens with a blacklist (USDC, USDT), the `safeTransfer` path will revert if the recipient is blacklisted: [5](#0-4) 

The nonce is therefore never permanently consumed, but the transfer can never succeed with the original recipient either. The tokens on NEAR remain permanently locked/burned.

---

### Impact Explanation

This matches **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds**.

Tokens are burned or locked on NEAR at `init_transfer` time. If the EVM recipient is permanently blacklisted, `finTransfer` will always revert. No governance action, no relayer retry, and no user action can recover the funds because the NEAR contract has no cancellation or recipient-update path.

---

### Likelihood Explanation

Moderate. The attack requires an adversary to:
1. Identify a pending NEAR → EVM transfer targeting a specific EOA.
2. Dust that EOA with proceeds from a known exploit.
3. Cause Circle (or another issuer) to blacklist the address.

This is most plausible when law enforcement requests blacklisting of an entire protocol address used as a bridge recipient, causing all in-flight transfers to that address to be permanently frozen — affecting many innocent users simultaneously.

---

### Recommendation

1. **Add a `cancel_transfer` function** that allows the original sender (verified via `transfer_message.sender`) to cancel a pending transfer and receive a refund of the locked/burned tokens, provided the transfer has not yet been finalized on the destination chain.
2. **Add a governance `update_recipient` action** (DAO-gated) to redirect a stuck pending transfer to a new recipient in emergency situations.
3. **Consider a transfer expiry / timeout mechanism** that automatically refunds the sender if a transfer has not been finalized within a configurable window.

---

### Proof of Concept

1. Alice calls `ft_on_transfer` on NEAR with an `InitTransfer` message specifying `recipient = "eth:0xAlice"`. Her tokens are burned/locked on NEAR and stored in `pending_transfers`. [6](#0-5) 

2. While the transfer awaits MPC signing, an attacker sends a small amount of stolen USDC to `0xAlice` on Ethereum.

3. Circle blacklists `0xAlice` on the USDC contract.

4. The relayer calls `sign_transfer` on NEAR; the MPC produces a valid signature over a payload with `recipient = 0xAlice`. [7](#0-6) 

5. The relayer calls `finTransfer` on the EVM `OmniBridge`. The call reaches the `safeTransfer` branch and reverts because USDC's `transfer` returns `false` for a blacklisted address. [5](#0-4) 

6. The EVM transaction reverts atomically; the destination nonce is not consumed. The transfer can be retried indefinitely, but will always revert as long as `0xAlice` remains blacklisted.

7. Alice's tokens remain permanently locked/burned on NEAR. There is no function in the NEAR bridge contract to cancel the transfer or redirect it to a different address. [2](#0-1)

### Citations

**File:** near/omni-bridge/src/lib.rs (L388-436)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L523-553)
```rust
    fn init_transfer(
        &mut self,
        sender_id: AccountId,
        signer_id: AccountId,
        token_id: AccountId,
        amount: U128,
        init_transfer_msg: InitTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );

        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-288)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L350-355)
```text
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```
