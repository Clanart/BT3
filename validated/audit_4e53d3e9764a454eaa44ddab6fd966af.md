### Title
No User-Callable Cancel Mechanism for Pending Transfers Causes Permanent Fund Lock — (`near/omni-bridge/src/lib.rs`)

### Summary
After a user initiates a NEAR-side bridge transfer via `ft_transfer_call`, their tokens are immediately burned (for deployed/bridged tokens) or locked in the bridge contract (for native tokens) and stored in `pending_transfers`. The only path to remove a transfer from `pending_transfers` and complete the flow requires a **trusted relayer** to call `sign_transfer`. There is no user-callable cancel or refund function. If no trusted relayer ever processes the transfer, the user's funds are permanently irrecoverable.

### Finding Description

When a user calls `ft_transfer_call` with an `InitTransfer` message, execution reaches `init_transfer_internal`:

```rust
fn init_transfer_internal(
    &mut self,
    transfer_message: TransferMessage,
    storage_owner: AccountId,
) -> U128 {
    // ...
    if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
        self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount); // burned immediately for deployed tokens
        self.lock_tokens_if_needed(
            transfer_message.get_destination_chain(),
            &token_id,
            transfer_message.amount.0,
        );
    }
    // ...
    env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
    U128(0)
}
``` [1](#0-0) 

For **deployed (bridged) tokens**, `burn_tokens_if_needed` destroys the tokens immediately and irreversibly. For **native tokens**, they are locked inside the bridge contract. In both cases, the transfer is stored in `pending_transfers`. [2](#0-1) 

The only way to remove a transfer from `pending_transfers` is through:
1. `sign_transfer_callback` (when fee is zero) — called only after a trusted relayer calls `sign_transfer`
2. `claim_fee_callback` — called only after a trusted relayer calls `claim_fee` with a destination-chain proof [3](#0-2) 

`sign_transfer` is gated by `#[trusted_relayer]`, meaning only accounts with the trusted relayer role can invoke it. The user themselves cannot call it:

```rust
#[trusted_relayer]
#[pause(except(roles(Role::DAO)))]
pub fn sign_transfer(
    &mut self,
    transfer_id: TransferId,
    ...
) -> Promise {
``` [4](#0-3) 

There is no `cancel_transfer`, `refund_transfer`, or any other user-callable function that removes a transfer from `pending_transfers` and returns locked tokens to the sender. The `update_transfer_fee` function only allows the sender to increase the fee — it does not cancel the transfer. [5](#0-4) 

The `pending_transfers` map has no expiry or timeout mechanism: [6](#0-5) 

### Impact Explanation

**For deployed (bridged) tokens**: Tokens are burned at `init_transfer_internal` time. If no relayer ever processes the transfer, those tokens are permanently destroyed — they cannot be recovered even with a future contract upgrade, because the token supply is already reduced.

**For native NEAR tokens**: Tokens remain locked in the bridge contract indefinitely. Without a cancel function, the user has no on-chain path to recover them.

This matches the allowed impact: **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user funds in bridge flows.**

### Likelihood Explanation

The scenario is triggered whenever a trusted relayer fails to process a specific pending transfer. Realistic triggers include:

- The user sets a fee too low to attract any relayer, and no relayer is willing to process it at zero profit.
- The relayer set is rotated (DAO removes all current relayers and adds new ones) and the new relayers do not process old pending transfers.
- The bridge is paused for an extended period; transfers initiated just before the pause remain in `pending_transfers` indefinitely.
- A relayer processes the MPC signing but the destination-chain finalization never happens, so `claim_fee` is never called and the transfer is never removed (for fee > 0 transfers).

The user has no recourse in any of these scenarios.

### Recommendation

Add a user-callable `cancel_transfer` function with a mandatory time-lock (e.g., 7 days after `init_transfer`). The function should:

1. Verify `env::predecessor_account_id()` matches `transfer_message.sender` (the original depositor).
2. Verify the transfer has been pending for longer than the time-lock.
3. For non-deployed tokens: return the locked amount to the sender via `ft_transfer`.
4. For deployed tokens: re-mint the burned amount back to the sender (requires the bridge to have mint authority, which it already has for deployed tokens).
5. Remove the entry from `pending_transfers` and restore the storage balance to the owner.

Additionally, expose a view function `is_transfer_signed(transfer_id)` so users and tooling can verify whether a transfer has been processed.

### Proof of Concept

1. User calls `ft_transfer_call` on a deployed bridged token contract with `msg = InitTransfer{recipient: eth_address, fee: U128(1), ...}`.
2. `init_transfer_internal` burns the user's tokens and stores the transfer in `pending_transfers`.
3. No trusted relayer calls `sign_transfer` (e.g., fee is too low, or relayer set is empty).
4. User attempts to recover funds — there is no `cancel_transfer` function to call.
5. User attempts `storage_withdraw` — this only returns NEAR storage deposit, not the bridged token amount.
6. The burned tokens are permanently gone; the `pending_transfers` entry remains indefinitely with no on-chain resolution path for the user. [7](#0-6) [8](#0-7)

### Citations

**File:** near/omni-bridge/src/lib.rs (L220-243)
```rust
pub struct Contract {
    pub factories: LookupMap<ChainKind, OmniAddress>,
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
    pub finalised_transfers: LookupSet<TransferId>,
    pub finalised_utxo_transfers: LookupSet<UnifiedTransferId>,
    pub fast_transfers: LookupMap<FastTransferId, FastTransferStatusStorage>,
    pub token_id_to_address: LookupMap<(ChainKind, AccountId), OmniAddress>,
    pub token_address_to_id: LookupMap<OmniAddress, AccountId>,
    pub token_decimals: LookupMap<OmniAddress, Decimals>,
    pub deployed_tokens: LookupSet<AccountId>,
    pub deployed_tokens_v2: LookupMap<AccountId, ChainKind>,
    pub token_deployer_accounts: LookupMap<ChainKind, AccountId>,
    pub mpc_signer: AccountId,
    pub current_origin_nonce: Nonce,
    // We maintain a separate nonce for each chain to optimize the storage usage on Solana by reducing the gaps.
    pub destination_nonces: LookupMap<ChainKind, Nonce>,
    pub accounts_balances: LookupMap<AccountId, StorageBalance>,
    pub wnear_account_id: AccountId,
    pub provers: UnorderedMap<ChainKind, AccountId>,
    pub init_transfer_promises: LookupMap<AccountId, CryptoHash>,
    pub utxo_chain_connectors: HashMap<ChainKind, UTXOChainConfig>,
    pub migrated_tokens: LookupMap<AccountId, AccountId>,
    pub locked_tokens: LookupMap<(ChainKind, AccountId), u128>,
}
```

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

**File:** near/omni-bridge/src/lib.rs (L444-452)
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
```

**File:** near/omni-bridge/src/lib.rs (L1648-1668)
```rust
            BridgeError::TokenAlreadyMigrated.as_ref()
        );

        ext_token::ext(new_token.clone())
            .with_static_gas(STORAGE_DEPOSIT_GAS)
            .with_attached_deposit(NEP141_DEPOSIT)
            .storage_deposit(&env::current_account_id(), Some(true))
            .detach();

        env::log_str(
            &OmniBridgeEvent::MigrateTokenEvent {
                old_token_id: old_token,
                new_token_id: new_token,
            }
            .to_log_string(),
        );
    }

    pub fn get_current_destination_nonce(&self, chain_kind: ChainKind) -> Nonce {
        self.destination_nonces.get(&chain_kind).unwrap_or_default()
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

**File:** near/omni-bridge/src/storage.rs (L186-212)
```rust
    #[payable]
    pub fn storage_withdraw(&mut self, amount: Option<NearToken>) -> StorageBalance {
        assert_one_yocto();
        let account_id = env::predecessor_account_id();
        let mut storage = self
            .storage_balance_of(&account_id)
            .near_expect(StorageError::AccountNotRegistered(account_id.clone()));
        let to_withdraw = amount.unwrap_or(storage.available);
        storage.total = storage.total.checked_sub(to_withdraw).near_expect(
            StorageError::NotEnoughStorageBalance {
                requested: to_withdraw,
                available: storage.total,
            },
        );
        storage.available = storage.available.checked_sub(to_withdraw).near_expect(
            StorageError::NotEnoughStorageBalance {
                requested: to_withdraw,
                available: storage.available,
            },
        );

        self.accounts_balances.insert(&account_id, &storage);

        Promise::new(account_id).transfer(to_withdraw).detach();

        storage
    }
```
