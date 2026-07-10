### Title
No Expiration or User-Callable Cancellation on `pending_transfers` Causes Permanent Fund Lock When Transfer Cannot Complete - (File: `near/omni-bridge/src/lib.rs`)

### Summary

Once a user initiates a cross-chain transfer via `init_transfer`, their tokens are immediately burned (for deployed/bridged tokens) or locked (for native tokens) and the transfer is inserted into `pending_transfers`. There is no expiration timestamp, no user-callable cancellation, and no timeout-based recovery mechanism on these entries. If the transfer cannot be completed on the destination chain for any reason — wrong token address mapping, destination chain unavailability, or permanent relayer failure — the user's funds are irrecoverably locked or burned with no protocol-level recovery path.

### Finding Description

`init_transfer_internal` in `near/omni-bridge/src/lib.rs` burns or locks user tokens and inserts the transfer into `pending_transfers`:

```rust
fn init_transfer_internal(&mut self, transfer_message: TransferMessage, storage_owner: AccountId) -> U128 {
    let required_storage_balance = self
        .add_transfer_message(transfer_message.clone(), storage_owner.clone())
        ...
    if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
        self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
        self.lock_tokens_if_needed(...);
    }
    ...
}
``` [1](#0-0) 

The only code paths that remove an entry from `pending_transfers` are:

1. `sign_transfer_callback` — only removes the entry when `fee.is_zero()`. When fee > 0, the entry persists after signing and must be removed by `claim_fee_callback`.
2. `claim_fee_callback` — requires a valid proof from the destination chain that a `FinTransfer` event was emitted.
3. `submit_transfer_to_utxo_chain_connector` — only for UTXO chains (BTC/Zcash). [2](#0-1) [3](#0-2) 

There is no:
- Expiration timestamp stored on `pending_transfers` entries
- User-callable cancellation or withdrawal function
- Timeout-based recovery mechanism

The `pending_transfers` map is a plain `LookupMap<TransferId, TransferMessageStorage>` with no time-bound metadata: [4](#0-3) 

### Impact Explanation

**For deployed/bridged tokens (burned on NEAR):** When `burn_tokens_if_needed` is called, the tokens are destroyed on NEAR. If the transfer cannot be finalized on the destination chain, the tokens are permanently gone — there is no mint-back mechanism and the DAO's `transfer_token_as_dao` cannot recover burned tokens.

**For native NEAR-origin tokens (locked in bridge):** Tokens are locked via `lock_tokens_if_needed`. If the transfer cannot be finalized, the tokens remain locked in the bridge contract indefinitely. While the DAO could theoretically call `transfer_token_as_dao`, this would corrupt the `locked_tokens` accounting and is not a sanctioned recovery path.

This matches the **Critical** impact class: *Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.*

### Likelihood Explanation

Realistic triggering conditions include:

1. **Wrong token address mapping registered for the destination chain** — the MPC signs a payload with the wrong `token_address`; the destination chain `fin_transfer` fails or mints to the wrong token; no `FinTransfer` proof is ever generated; `claim_fee` can never be called.
2. **Destination chain bridge contract upgraded/replaced** — the factory address changes; old pending transfers reference the old factory; `claim_fee_callback` rejects the proof because `self.factories.get(&chain) != Some(emitter_address)`.
3. **Permanent relayer failure after tokens are burned/locked** — no relayer ever calls `sign_transfer` or `claim_fee`; the user has no self-service recovery. [5](#0-4) 

The user-controlled entry path is fully permissionless: any user can call `ft_transfer_call` on any registered token, which triggers `ft_on_transfer` → `init_transfer` → `init_transfer_internal`, burning/locking tokens and inserting the entry. [6](#0-5) 

### Recommendation

Add a user-callable cancellation function with a grace period (e.g., 7 days after transfer creation, analogous to the Sablier fix), allowing the original sender to cancel a pending transfer and recover their tokens:

- For deployed tokens: mint back the burned amount to the sender.
- For native tokens: unlock and return the locked amount to the sender.
- Store a `created_at` timestamp in `TransferMessageStorage` to enforce the grace period.
- Restrict cancellation to the original `sender` field of the `TransferMessage`.

Alternatively, store a deadline in each `pending_transfers` entry and allow cancellation only after the deadline passes without finalization.

### Proof of Concept

1. User calls `ft_transfer_call` on a deployed bridged token (e.g., `eth.bridge.near`) with `InitTransfer` message targeting an EVM chain with fee > 0.
2. `init_transfer_internal` burns the tokens via `burn_tokens_if_needed` and inserts the entry into `pending_transfers`.
3. The DAO subsequently updates the factory address for the destination chain (bridge upgrade scenario).
4. A relayer calls `sign_transfer` — this succeeds because the token address mapping is still valid.
5. The relayer attempts `fin_transfer` on the destination chain — it fails because the emitter address no longer matches the registered factory.
6. No `FinTransfer` event is ever emitted on the destination chain.
7. `claim_fee` is called but `claim_fee_callback` panics at the factory check: `self.factories.get(&chain) != Some(emitter_address)`.
8. The transfer entry remains in `pending_transfers` indefinitely. The user's tokens are permanently burned. There is no function the user can call to recover them. [7](#0-6) [8](#0-7)

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

**File:** near/omni-bridge/src/lib.rs (L252-283)
```rust
    #[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
        let token_id = env::predecessor_account_id();
        let parsed_msg: BridgeOnTransferMsg = serde_json::from_str(&msg)
            .or_else(|_| serde_json::from_str(&msg).map(BridgeOnTransferMsg::InitTransfer))
            .near_expect(BridgeError::ParseMsg);

        // We can't trust sender_id to pay for storage as it can be spoofed.
        let signer_id = env::signer_account_id();
        let promise_or_promise_index_or_value = match parsed_msg {
            BridgeOnTransferMsg::InitTransfer(init_transfer_msg) => {
                self.init_transfer(sender_id, signer_id, token_id, amount, init_transfer_msg)
            }
            BridgeOnTransferMsg::FastFinTransfer(fast_fin_transfer_msg) => {
                self.fast_fin_transfer(token_id, amount, signer_id, fast_fin_transfer_msg)
            }
            BridgeOnTransferMsg::UtxoFinTransfer(utxo_fin_transfer_msg) => self.utxo_fin_transfer(
                token_id,
                amount,
                &signer_id,
                &sender_id,
                utxo_fin_transfer_msg,
            ),
            BridgeOnTransferMsg::SwapMigratedToken => {
                self.swap_migrated_token(sender_id, token_id, amount)
                    .detach();
                PromiseOrPromiseIndexOrValue::Value(U128(0))
            }
        };

        promise_or_promise_index_or_value.as_return();
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

**File:** near/omni-bridge/src/lib.rs (L1086-1094)
```rust
        );
        require!(
            self.factories
                .get(&fin_transfer.emitter_address.get_chain())
                == Some(fin_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
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
