### Title
Pending transfers in `pending_transfers` never expire and have no user-initiated cancel/reclaim path — (File: near/omni-bridge/src/lib.rs)

---

### Summary

When a user initiates a NEAR-to-other-chain transfer via `ft_transfer_call` → `init_transfer`, their tokens are locked inside the bridge contract and a `TransferMessage` is inserted into `pending_transfers`. No expiration timestamp is recorded, no deadline is enforced, and no public cancel or reclaim function exists. The transfer remains live indefinitely until a trusted relayer calls `sign_transfer`. If the relayer set becomes unavailable for any reason, the user's tokens are irrecoverably frozen with no protocol-level escape hatch.

---

### Finding Description

`init_transfer` stores a `TransferMessage` in `pending_transfers` with no time-bound field:

```rust
pub struct Contract {
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
    ...
}
``` [1](#0-0) 

The `TransferMessage` struct carries no `created_at`, `expires_at`, or deadline field. After insertion the only removal paths are internal:

- `remove_transfer_message` — called from `sign_transfer_callback` **only when `fee.is_zero()`**
- `remove_transfer_message_without_refund` — called in specific internal refund paths [2](#0-1) [3](#0-2) 

For **fee-bearing transfers** (the common production case), `sign_transfer_callback` explicitly skips removal:

```rust
if let Ok(signature) = call_result {
    if fee.is_zero() {
        self.remove_transfer_message(message_payload.transfer_id);
    }
    // transfer stays in pending_transfers forever for fee > 0
    ...
}
``` [4](#0-3) 

`sign_transfer` itself is gated by `#[trusted_relayer]` — only trusted relayers or DAO can call it: [5](#0-4) 

There is no public `cancel_transfer`, `reclaim_tokens`, or equivalent function anywhere in the contract. The user has no unilateral way to recover their locked tokens once `init_transfer` completes.

---

### Impact Explanation

**Impact: Critical — Permanent freezing / irrecoverable lock of user funds.**

A user's tokens are transferred into the bridge contract via `ft_transfer_call` and locked there. The only release path requires a trusted relayer to call `sign_transfer` and subsequently finalize on the destination chain. Because:

1. No expiration exists on the pending transfer, and
2. No user-callable cancel/reclaim function exists,

if the relayer set becomes unavailable (all relayers removed by DAO, relayer infrastructure failure, bridge paused indefinitely, or the trusted-relayer list is rotated and old transfers are abandoned), the user's tokens are permanently frozen inside the bridge with zero recourse. The locked-token accounting (`locked_tokens`) is never corrected, and the `pending_transfers` entry persists forever.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Likelihood: Medium.**

The scenario does not require a malicious actor — it requires only relayer unavailability, which is a realistic operational condition:

- The DAO can remove all trusted relayers via `acl_revoke_role` at any time.
- Relayer infrastructure can fail or be decommissioned.
- The bridge can be paused indefinitely (via `PauseManager`), blocking `sign_transfer` while leaving tokens locked.
- A user who initiated a transfer just before a pause or relayer rotation has no recourse.

The entry path is fully unprivileged: any token holder can call `ft_transfer_call` to initiate a transfer, locking their tokens.

---

### Recommendation

1. **Add an expiration field** to `TransferMessage` (e.g., `created_at: u64` block timestamp or `expires_at: u64`).
2. **Add a public `cancel_transfer` function** callable by the original sender after the expiration window, which removes the entry from `pending_transfers` and returns the locked tokens to the sender.
3. For fee-bearing transfers that have already been signed, ensure the cancel path also handles the case where an MPC signature was already emitted (e.g., by checking `finalised_transfers` before releasing).

---

### Proof of Concept

1. Alice calls `ft_transfer_call` on token contract with `msg = InitTransfer { recipient: EVM_ADDR, fee: 100, ... }`.
2. Bridge receives tokens, calls `init_transfer`, increments `current_origin_nonce`, stores `TransferMessage` in `pending_transfers`. Alice's tokens are now held by the bridge.
3. The DAO removes all trusted relayers (or the bridge is paused indefinitely).
4. `sign_transfer` is now uncallable (no trusted relayer exists / bridge paused).
5. Alice has no `cancel_transfer` function to call.
6. Alice's tokens remain locked in `pending_transfers` forever — no expiration fires, no escape hatch exists. [6](#0-5) [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L222-222)
```rust
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
```

**File:** near/omni-bridge/src/lib.rs (L245-249)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedRelayer),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

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

**File:** near/omni-bridge/src/lib.rs (L523-558)
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
