### Title
In-Flight Pending Transfers Permanently Bricked by `migrate_deployed_token` — (`near/omni-bridge/src/lib.rs`)

### Summary

`migrate_deployed_token` atomically removes the old NEAR token account from all bridge lookup maps and replaces it with a new one. Any `pending_transfers` entry that was created before the migration and references the old token account can never be signed or completed afterward, permanently locking the user's already-burned tokens.

### Finding Description

The `migrate_deployed_token` function (DAO-only) performs the following state changes atomically:

1. Removes `old_token` from `deployed_tokens` and `deployed_tokens_v2`.
2. Removes `(origin_chain, old_token)` from `token_id_to_address`.
3. Inserts `(origin_chain, new_token)` into `token_id_to_address`.
4. Overwrites `token_address_to_id[origin_address]` with `new_token`.
5. Records `migrated_tokens[old_token] = new_token`. [1](#0-0) 

The bridge's two-step NEAR-to-EVM transfer flow is:

- **Step 1 (`ft_on_transfer` → `init_transfer_internal`):** The user sends `old_token` to the bridge. The bridge burns the tokens (because `old_token` is a deployed/bridged token) and stores a `TransferMessage` in `pending_transfers` with `token = OmniAddress::Near(old_token)`. [2](#0-1) 

- **Step 2 (`sign_transfer`):** A relayer calls `sign_transfer(transfer_id, ...)`. This function resolves the destination-chain token address by calling `get_token_address(destination_chain, get_token_id(&transfer_message.token))`, which expands to `token_id_to_address.get(&(destination_chain, old_token))`. [3](#0-2) 

If `migrate_deployed_token` is called between Step 1 and Step 2, `(destination_chain, old_token)` no longer exists in `token_id_to_address`. The lookup returns `None`, and `sign_transfer` panics with `FailedToGetTokenAddress`: [3](#0-2) 

The `pending_transfers` entry remains in storage forever (no expiry, no admin removal function), and the tokens that were burned in Step 1 are irrecoverable. The `swap_migrated_token` helper only swaps held balances of `old_token` for `new_token`; it does not rescue pending transfers. [4](#0-3) 

There is no function in the contract that allows updating the `token` field of an existing `pending_transfers` entry, and no admin escape hatch to cancel a pending transfer and refund the burned tokens. [5](#0-4) 

### Impact Explanation

Any user who initiated a NEAR-to-EVM transfer of `old_token` before the DAO called `migrate_deployed_token` will have their tokens permanently lost:

- The tokens were burned at `init_transfer_internal` time.
- `sign_transfer` will always panic for that `TransferId`.
- No MPC signature is ever produced, so the destination chain never releases funds.
- The `pending_transfers` entry is irremovable by the user.

This matches **Critical — Permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation

- Deployed tokens (bridged EVM tokens on NEAR) are actively used. Pending transfers for such tokens exist at any point in time.
- The DAO has a legitimate operational reason to call `migrate_deployed_token` (e.g., token contract upgrade). There is no on-chain guard that checks for outstanding `pending_transfers` before allowing the migration.
- The window between a user's `ft_transfer_call` and the relayer's `sign_transfer` can span multiple blocks, making a race condition realistic.
- The DAO need not be malicious; a routine migration during normal bridge activity is sufficient to trigger the loss.

### Recommendation

Before executing the token mapping swap, `migrate_deployed_token` should verify that no pending transfers reference `old_token`. If such transfers exist, the migration should either be rejected or deferred. Alternatively, introduce a two-phase migration:

1. **Phase 1:** Mark `old_token` as "migration pending" — block new `init_transfer` calls for it but allow existing `sign_transfer` calls to complete.
2. **Phase 2:** Once `pending_transfers` for `old_token` is empty, perform the mapping swap.

Additionally, add an admin function to cancel a stuck pending transfer and re-mint (or otherwise compensate) the burned tokens to the original sender.

### Proof of Concept

1. Alice calls `ft_transfer_call` on `old_token.near`, sending 1000 tokens to the bridge with `msg = InitTransfer{recipient: EVM_ADDR, ...}`.
2. Bridge burns 1000 `old_token` and stores `pending_transfers[{Near, nonce=42}] = TransferMessage{token: OmniAddress::Near("old_token.near"), ...}`.
3. DAO calls `migrate_deployed_token(Eth, "old_token.near", "new_token.near")`.
   - `token_id_to_address.remove(&(Eth, "old_token.near"))` executes.
4. Relayer calls `sign_transfer({Near, 42}, ...)`.
   - `get_token_id(&OmniAddress::Near("old_token.near"))` → `"old_token.near"`.
   - `get_token_address(Eth, "old_token.near")` → `None` (removed in step 3).
   - Contract panics: `FailedToGetTokenAddress`.
5. Alice's 1000 tokens are burned and permanently lost. The pending transfer entry remains in storage indefinitely with no recovery path. [6](#0-5) [3](#0-2)

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

**File:** near/omni-bridge/src/lib.rs (L462-469)
```rust
        let token_address = self
            .get_token_address(
                transfer_message.get_destination_chain(),
                self.get_token_id(&transfer_message.token),
            )
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });
```

**File:** near/omni-bridge/src/lib.rs (L1604-1664)
```rust
    #[access_control_any(roles(Role::DAO))]
    #[payable]
    pub fn migrate_deployed_token(
        &mut self,
        origin_chain: ChainKind,
        old_token: AccountId,
        new_token: AccountId,
    ) {
        require!(
            env::attached_deposit() >= NEP141_DEPOSIT,
            BridgeError::NotEnoughAttachedDeposit.as_ref()
        );

        require!(
            self.deployed_tokens.remove(&old_token),
            BridgeError::OldTokenNotDeployed.as_ref(),
        );
        require!(
            self.deployed_tokens.insert(&new_token),
            BridgeError::TokenExists.as_ref()
        );
        self.deployed_tokens_v2.remove(&old_token);
        self.deployed_tokens_v2.insert(&new_token, &origin_chain);

        let origin_address = self
            .token_id_to_address
            .remove(&(origin_chain, old_token.clone()))
            .near_expect(BridgeError::FailedToGetTokenAddress);

        require!(
            self.token_id_to_address
                .insert(&(origin_chain, new_token.clone()), &origin_address)
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );

        self.token_address_to_id
            .insert(&origin_address, &new_token)
            .near_expect(BridgeError::ExpectedToOverwriteTokenAddress);

        require!(
            self.migrated_tokens
                .insert(&old_token, &new_token)
                .is_none(),
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

**File:** near/omni-bridge/src/lib.rs (L2738-2753)
```rust
    fn swap_migrated_token(
        &mut self,
        sender_id: AccountId,
        old_token: AccountId,
        amount: U128,
    ) -> Promise {
        let new_token = self
            .migrated_tokens
            .get(&old_token)
            .near_expect(BridgeError::TokenNotMigrated);

        let burn = ext_token::ext(old_token).burn(amount);
        let mint = ext_token::ext(new_token).mint(sender_id, amount, None);

        burn.and(mint)
    }
```
