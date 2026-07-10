### Title
Blacklisted Recipient Permanently Freezes Bridged Funds with No Recovery Path - (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

When a user bridges a blacklistable token (e.g., USDC, USDT) from NEAR to EVM (or StarkNet), the destination recipient address is cryptographically fixed in the MPC-signed payload. If that recipient address is subsequently blacklisted by the token contract before `finTransfer` is called, every attempt to finalize the transfer will revert. Because the recipient is immutable in the signed message and there is no cancel/refund path back to the source chain, the locked/burned tokens on NEAR are permanently irrecoverable.

---

### Finding Description

**EVM — `OmniBridge.finTransfer`**

The `finTransfer` function in `OmniBridge.sol` finalizes an inbound bridge transfer by transferring tokens to `payload.recipient`:

```solidity
// OmniBridge.sol line 287
completedTransfers[payload.destinationNonce] = true;
// ...
// line 351-354
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount
);
``` [1](#0-0) [2](#0-1) 

If `payload.recipient` is blacklisted by the token (e.g., USDC's `_blacklist`), `safeTransfer` reverts. The entire transaction reverts (including the nonce marking at line 287), so the nonce is not permanently consumed — but the transfer can never succeed because the recipient is fixed.

**Why the recipient is immutable:** On NEAR, `sign_transfer` constructs a `TransferMessagePayload` that includes the exact `recipient` field and submits it to the MPC signer:

```rust
let transfer_payload = TransferMessagePayload {
    // ...
    recipient: transfer_message.recipient,
    // ...
};
// submitted to MPC for signing
ext_signer::ext(self.mpc_signer.clone()).sign(SignRequest { payload, ... })
``` [3](#0-2) 

The MPC signature covers the full payload including `recipient`. There is no mechanism to re-sign with a different recipient for the same transfer, and no cancel/refund path exists to return tokens to the source chain sender.

**Source-chain lock is permanent:** When a user calls `ft_transfer_call` on NEAR to initiate a transfer, tokens are locked or burned. The `process_fin_transfer_to_other_chain` function on NEAR records the transfer and adjusts `locked_tokens` accounting, but there is no corresponding "cancel transfer" or "return to sender" function: [4](#0-3) 

The `fin_transfer_send_tokens_callback` refund path only handles NEAR-to-NEAR `ft_transfer_call` failures — it does not handle the case where the EVM `finTransfer` permanently fails: [5](#0-4) 

**StarkNet — `omni_bridge.fin_transfer`**

The same pattern exists on StarkNet. The nonce is marked used before the transfer, and if the ERC-20 `transfer` returns `false` (blacklisted recipient), the `assert` reverts the whole transaction:

```cairo
_set_transfer_finalised(ref self, payload.destination_nonce);  // line 250
// ...
let success = IERC20Dispatcher { contract_address: payload.token_address }
    .transfer(payload.recipient, payload.amount.into());
assert(success, 'ERR_TRANSFER_FAILED');  // line 262
``` [6](#0-5) 

Same result: the transfer can never succeed, and there is no recovery path.

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

Once a user initiates a NEAR→EVM (or NEAR→StarkNet) transfer of a blacklistable token to a recipient that becomes blacklisted:

1. Tokens are locked/burned on NEAR — irreversible.
2. `finTransfer` on EVM/StarkNet will always revert for that recipient.
3. No cancel, redirect, or refund mechanism exists.
4. The funds are permanently frozen in the bridge with no recovery path for the user or the protocol.

---

### Likelihood Explanation

**Moderate.** USDC and USDT are among the most commonly bridged tokens and both implement address blacklists. A user could initiate a bridge transfer to an EVM address that is subsequently sanctioned (e.g., by OFAC action, exchange compliance, or contract exploit attribution) before the relayer calls `finTransfer`. The window between `init_transfer` on NEAR and `finTransfer` on EVM can span minutes to hours depending on relayer activity, MPC signing latency, and fee negotiation. This is a realistic scenario for high-value transfers.

---

### Recommendation

1. **Add a fallback recipient or claims mechanism on EVM/StarkNet:** If `safeTransfer` to `payload.recipient` fails, store the amount in a `claims[token][recipient]` mapping. Allow the protocol admin (not an arbitrary caller) to redirect unclaimed funds after a timeout, or allow the original sender to reclaim via a separate proof.

2. **Add a cancel/refund path on NEAR:** Introduce a `cancel_transfer` function that, given proof that `finTransfer` has permanently failed (e.g., after N failed attempts or admin attestation), unlocks/re-mints tokens back to the original sender on NEAR.

3. **Use try/catch pattern on EVM:** Wrap the `safeTransfer` in a try/catch so that a failed transfer does not revert the nonce marking, and instead records the claimable amount:

```solidity
try IERC20(payload.tokenAddress).transfer(payload.recipient, payload.amount) returns (bool ok) {
    if (!ok) claims[payload.tokenAddress][payload.recipient] += payload.amount;
} catch {
    claims[payload.tokenAddress][payload.recipient] += payload.amount;
}
```

---

### Proof of Concept

1. Alice holds 10,000 USDC on NEAR and calls `ft_transfer_call` to the NEAR bridge contract, specifying `recipient = "eth:0xAlice"`. Tokens are locked on NEAR.
2. NEAR bridge stores the `TransferMessage` and the relayer calls `sign_transfer`. MPC signs a payload with `recipient = 0xAlice` embedded.
3. Before the relayer submits `finTransfer` on EVM, `0xAlice` is added to USDC's blacklist (e.g., OFAC sanction).
4. Relayer calls `OmniBridge.finTransfer(signatureData, payload)` on EVM.
5. Execution reaches `IERC20(usdc).safeTransfer(0xAlice, 10000e6)` — this reverts because `0xAlice` is blacklisted.
6. The entire transaction reverts. The nonce is not consumed.
7. Every subsequent retry of `finTransfer` with the same MPC-signed payload also reverts.
8. No alternative signed payload exists (MPC signed exactly `0xAlice`).
9. Alice's 10,000 USDC is permanently locked in the NEAR bridge contract with no recovery path. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L1692-1718)
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

**File:** starknet/src/omni_bridge.cairo (L242-279)
```text
        fn fin_transfer(
            ref self: ContractState, signature: Signature, payload: TransferMessagePayload,
        ) {
            assert(!_is_paused(@self, PAUSE_FIN_TRANSFER), 'ERR_FIN_TRANSFER_PAUSED');

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

            self
                .emit(
                    Event::FinTransfer(
                        FinTransfer {
                            origin_chain: payload.origin_chain,
                            origin_nonce: payload.origin_nonce,
                            token_address: payload.token_address,
                            amount: payload.amount,
                            recipient: payload.recipient,
                            fee_recipient: payload.fee_recipient,
                            message: payload.message,
                        },
                    ),
                )
        }
```
