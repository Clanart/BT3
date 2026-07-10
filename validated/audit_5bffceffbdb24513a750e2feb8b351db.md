### Title
No User-Controlled Cancel/Refund Mechanism for Pending Transfers — (`near/omni-bridge/src/lib.rs`)

### Summary

Once a user initiates a cross-chain transfer from NEAR, their tokens are immediately locked or burned and a `TransferMessage` is stored in `pending_transfers`. There is no function anywhere in the codebase that allows the originating user to cancel the transfer and recover their funds. The only paths that remove a pending transfer message are gated on trusted-relayer action (`sign_transfer`, `claim_fee`) or proof of destination-chain finalization. If the relayer pipeline stalls, MPC signing fails permanently, or the destination chain never finalizes, the user's funds are irrecoverably locked with no escape hatch.

### Finding Description

The NEAR-origin transfer lifecycle is:

1. **`init_transfer_internal`** — tokens are locked (`lock_tokens_if_needed`) or burned (`burn_tokens_if_needed`) and the `TransferMessage` is inserted into `pending_transfers`. [1](#0-0) 

2. **`sign_transfer`** (gated `#[trusted_relayer]`) — a trusted relayer requests an MPC signature over the transfer payload. [2](#0-1) 

3. **`sign_transfer_callback`** — the message is removed from `pending_transfers` **only when `fee.is_zero()`**. For any transfer with a non-zero fee the message remains stored indefinitely. [3](#0-2) 

4. **`claim_fee`** (gated `#[trusted_relayer]`) — after the relayer submits the signed payload to the destination chain and obtains a finalization proof, it calls `claim_fee`, which calls `remove_transfer_message` and pays out the fee. [4](#0-3) 

A grep across the entire codebase for any `cancel`, `abort`, or `revert_transfer` function returns zero results. There is no public or user-callable function that removes a pending transfer and returns the locked/burned tokens to the sender. [5](#0-4) 

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds** (Critical per allowed impact scope).

Scenarios that leave funds permanently locked:

- **MPC signing failure**: `sign_transfer_callback` silently does nothing when `call_result` is `Err`. The transfer message stays in `pending_transfers`, tokens remain locked/burned, and the user has no recourse. [3](#0-2) 

- **Relayer inaction**: `sign_transfer` is `#[trusted_relayer]`-gated. If no active relayer services the transfer (relayer set shrinks, relayer goes offline), the transfer message sits in `pending_transfers` forever. [6](#0-5) 

- **Destination chain never finalizes**: Even after a successful MPC signature, if the relayer never submits the payload to the destination chain (or the destination chain is paused), `claim_fee` is never called, the message is never removed, and the locked/burned tokens on NEAR are unrecoverable. [7](#0-6) 

In all cases the user's tokens are already gone from their wallet (locked in the bridge or burned) with no on-chain path to recover them.

### Likelihood Explanation

- MPC signing is an external async call that can fail due to network issues, MPC node downtime, or quota exhaustion; the callback silently swallows the error.
- The trusted-relayer set is a small, permissioned group; any operational disruption leaves all in-flight transfers stranded.
- The destination chain (`finTransfer` on EVM, `finalize_transfer` on Solana) can be paused by its admin, blocking the finalization proof needed for `claim_fee`.
- Any ordinary user who calls `ft_transfer_call` with an `InitTransfer` message is exposed to this risk with no opt-out.

### Recommendation

Add a user-callable `cancel_transfer` function that:
1. Verifies the caller is the original `sender` stored in the `TransferMessage`.
2. Enforces a minimum timeout (e.g., transfer has been pending for N blocks/epochs) to prevent griefing of in-flight relayer operations.
3. Removes the message from `pending_transfers` via `remove_transfer_message`.
4. Unlocks (`unlock_tokens_if_needed`) or re-mints (`mint`) the tokens back to the sender.

This mirrors the cancel-on-destination-domain pattern described in the referenced Connext/Spearbit report and is the standard escape hatch for stuck cross-chain transfers.

### Proof of Concept

```
1. Alice calls ft_transfer_call on her NEP-141 token with msg = InitTransfer{fee: 100, recipient: EVM_ADDR, ...}
   → init_transfer_internal runs:
       burn_tokens_if_needed(alice_token, amount)   // tokens gone from Alice
       add_transfer_message(transfer_msg, alice)    // stored in pending_transfers
   → Alice's tokens are now locked/burned.

2. The MPC network is temporarily unavailable.
   → sign_transfer is called by the relayer but sign_transfer_callback receives Err.
   → The callback does nothing; pending_transfers still holds Alice's transfer.

3. Alice has no function to call.
   → get_transfer_message(transfer_id) confirms the message still exists.
   → No cancel/abort/refund function exists in the contract.
   → Alice's funds are permanently locked.
``` [8](#0-7) [9](#0-8)

### Citations

**File:** near/omni-bridge/src/lib.rs (L444-521)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
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

**File:** near/omni-bridge/src/lib.rs (L1054-1064)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args).then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(env::attached_deposit())
                .with_static_gas(CLAIM_FEE_CALLBACK_GAS)
                .claim_fee_callback(&env::predecessor_account_id()),
        )
    }
```

**File:** near/omni-bridge/src/lib.rs (L1829-1865)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
    }
```

**File:** near/omni-bridge/src/lib.rs (L2194-2224)
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

    fn remove_transfer_message_without_refund(
        &mut self,
        transfer_id: TransferId,
    ) -> TransferMessage {
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        transfer.message
    }
```
