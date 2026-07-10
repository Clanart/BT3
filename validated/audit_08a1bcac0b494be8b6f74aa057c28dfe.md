### Title
Permanent Fund Lock When Destination Recipient Is Blacklisted in Transfer-Restricted Token — (`near/omni-bridge/src/lib.rs`)

### Summary

The NEAR bridge contract locks or burns user tokens upon `init_transfer` and stores the pending transfer in `pending_transfers`. There is no cancel or refund function for pending transfers. If the destination recipient address becomes blacklisted in a transfer-restricted token (e.g., USDC, USDT) after the transfer is initiated, `finTransfer` on the destination chain will always revert, and the user's tokens are permanently locked in the NEAR bridge with no recovery path.

### Finding Description

When a user initiates a cross-chain transfer from NEAR, the `init_transfer` internal function is called via `ft_on_transfer`. The tokens are immediately locked (for non-deployed tokens) or burned (for deployed tokens) and a `TransferMessage` is inserted into `pending_transfers`. [1](#0-0) 

The transfer is then signed by the MPC via `sign_transfer`, which produces a signature over a payload that includes the recipient address as a fixed field. [2](#0-1) 

On the EVM destination, `finTransfer` in `OmniBridge.sol` attempts to deliver tokens to `payload.recipient`. For a non-bridge-token (e.g., USDC held in the bridge), it calls `safeTransfer`: [3](#0-2) 

If `payload.recipient` is blacklisted in USDC/USDT, `safeTransfer` reverts. The entire `finTransfer` transaction reverts (including the `completedTransfers[nonce] = true` assignment at line 287), so the nonce is not permanently consumed. However, the recipient address is immutably encoded in the MPC-signed payload — it cannot be changed without a new MPC signature, and the NEAR bridge provides no mechanism to update the recipient of a pending transfer. [4](#0-3) 

The same pattern exists on Starknet: `_set_transfer_finalised` is called before the token transfer, but if the transfer panics, the state rolls back. The recipient is still fixed. [5](#0-4) 

Critically, the NEAR bridge has **no cancel or refund function** for pending transfers. The only functions that remove a `TransferMessage` from `pending_transfers` are `sign_transfer_callback` (when fee is zero, after successful signing) and `claim_fee_callback` (after successful finalization on the destination). Neither is callable by the user to recover locked tokens. [6](#0-5) 

The `update_transfer_fee` function allows updating the fee but not the recipient address. [7](#0-6) 

### Impact Explanation

A user's tokens are permanently and irrecoverably locked in the NEAR bridge contract. The locked tokens cannot be retrieved by the user, cannot be redirected to a different recipient, and cannot be finalized on the destination chain. This matches the allowed impact: **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

USDC and USDT both implement address blacklisting and are among the most commonly bridged assets. Blacklisting can occur due to regulatory action, sanctions compliance, or exchange-level enforcement. A user whose address is blacklisted after initiating a bridge transfer (but before finalization) will permanently lose their funds. This is a realistic scenario with no user error required.

### Recommendation

Add a user-callable `cancel_transfer` function on the NEAR bridge that:
1. Verifies the caller is the original sender of the pending transfer.
2. Removes the `TransferMessage` from `pending_transfers`.
3. Returns the locked tokens to the sender (or to a caller-specified recovery address).

This mirrors the fix recommended in M-08: allow the depositor to specify a recovery address rather than hardcoding the original sender.

```rust
pub fn cancel_transfer(&mut self, transfer_id: TransferId, refund_to: AccountId) {
    let transfer = self.get_transfer_message(transfer_id);
    require!(
        transfer.sender == OmniAddress::Near(env::predecessor_account_id()),
        "Only sender can cancel"
    );
    self.remove_transfer_message(transfer_id);
    // return tokens to refund_to
    self.send_tokens(token, refund_to, transfer.amount, "");
}
```

### Proof of Concept

1. User holds 10,000 USDC on NEAR and calls `ft_on_transfer` with an `InitTransfer` message targeting their EVM address `0xABCD...` on Ethereum. USDC is locked in the NEAR bridge.
2. A relayer calls `sign_transfer` — the MPC signs a payload with `recipient = 0xABCD...`.
3. Before the relayer submits `finTransfer` on Ethereum, Circle blacklists `0xABCD...` in the USDC contract.
4. The relayer calls `finTransfer` on Ethereum. `IERC20(usdc).safeTransfer(0xABCD..., 10000e6)` reverts. The transaction reverts. The nonce is not consumed.
5. No matter how many times the relayer retries, `finTransfer` always reverts.
6. The user cannot call any function on the NEAR bridge to cancel the transfer and recover their USDC.
7. The 10,000 USDC is permanently locked in the NEAR bridge contract. [8](#0-7) [9](#0-8)

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

**File:** near/omni-bridge/src/lib.rs (L491-500)
```rust
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
```

**File:** near/omni-bridge/src/lib.rs (L523-619)
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
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );

        let required_storage_balance =
            self.required_balance_for_init_transfer_message(transfer_message.clone());

        let message_storage_account_id = transfer_message
            .calculate_storage_account_id(init_transfer_msg.external_id.map(String::from));

        // Choose storage payer or whether to yield execution until storage is available
        if self
            .try_to_transfer_balance_from_message_account(
                &message_storage_account_id,
                NearToken::from_yoctonear(init_transfer_msg.native_token_fee.0),
                &signer_id,
                required_storage_balance,
            )
            .is_ok()
            || (self.has_storage_balance(
                &signer_id,
                required_storage_balance.saturating_add(NearToken::from_yoctonear(
                    init_transfer_msg.native_token_fee.0,
                )),
            ) && (init_transfer_msg.native_token_fee.0 == 0
                || !self.acl_has_role(Role::NativeFeeRestricted.into(), signer_id.clone())))
        {
            PromiseOrPromiseIndexOrValue::Value(
                self.init_transfer_internal(transfer_message, signer_id),
            )
        } else {
            let promise_index = env::promise_yield_create(
                "init_transfer_resume",
                json!({
                    "transfer_message": transfer_message,
                    "message_storage_account_id": message_storage_account_id,
                    "storage_owner": signer_id,
                })
                .to_string()
                .as_bytes(),
                INIT_TRANSFER_RESUME_GAS,
                GasWeight(0),
                PROMISE_REGISTER_ID,
            );

            let yield_id: CryptoHash = env::read_register(PROMISE_REGISTER_ID)
                .near_expect(BridgeError::ReadPromiseRegister)
                .try_into()
                .near_expect(BridgeError::ReadPromiseYieldId);

            let required_storage_balance = self.add_promise(&message_storage_account_id, &yield_id);

            self.update_storage_balance(
                env::current_account_id(),
                required_storage_balance,
                NearToken::from_yoctonear(0),
            );

            env::log_str(&format!(
                "Yield init transfer until storage is available at {message_storage_account_id}"
            ));

            PromiseOrPromiseIndexOrValue::PromiseIndex(promise_index)
        }
    }
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-367)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
        } else if (customMinters[payload.tokenAddress] != address(0)) {
            ICustomMinter(customMinters[payload.tokenAddress]).mint(
                payload.tokenAddress,
                payload.recipient,
                payload.amount
            );
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }

        finTransferExtension(payload);

        emit BridgeTypes.FinTransfer(
            payload.originChain,
            payload.originNonce,
            payload.tokenAddress,
            payload.amount,
            payload.recipient,
            payload.feeRecipient
        );
    }
```

**File:** starknet/src/omni_bridge.cairo (L247-263)
```text
            assert(
                !self.is_transfer_finalised(payload.destination_nonce), 'ERR_NONCE_ALREADY_USED',
            );
            _set_transfer_finalised(ref self, payload.destination_nonce);

            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );

            if self.is_bridge_token(payload.token_address) {
                IBridgeTokenDispatcher { contract_address: payload.token_address }
                    .mint(payload.recipient, payload.amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: payload.token_address }
                    .transfer(payload.recipient, payload.amount.into());
                assert(success, 'ERR_TRANSFER_FAILED');
            }
```
