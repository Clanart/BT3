### Title
`migrate_deployed_token` Does Not Migrate `locked_tokens` State, Breaking Cross-Chain Accounting — (`File: near/omni-bridge/src/lib.rs`)

### Summary

`migrate_deployed_token` updates several token-identity mappings when renaming a deployed token from `old_token` to `new_token`, but it never migrates the `locked_tokens` map. After migration, every `lock_tokens_if_needed` / `unlock_tokens_if_needed` call for `new_token` on any non-origin chain silently returns `LockAction::Unchanged`, permanently destroying the bridge's cross-chain supply accounting for that token.

### Finding Description

`migrate_deployed_token` (DAO-callable) updates:

- `deployed_tokens` — removes `old_token`, inserts `new_token` ✓
- `deployed_tokens_v2` — removes `old_token`, inserts `new_token` with `origin_chain` ✓
- `token_id_to_address[(origin_chain, old_token)]` — removed; `(origin_chain, new_token)` inserted ✓
- `token_address_to_id[origin_address]` — updated to `new_token` ✓
- `migrated_tokens[old_token]` — set to `new_token` ✓

What it **never** touches:

```
locked_tokens: LookupMap<(ChainKind, AccountId), u128>
``` [1](#0-0) 

Before migration, `bind_token_callback` initialised `locked_tokens[(origin_chain, old_token)] = 0` and subsequent transfers accumulated balances such as `locked_tokens[(Sol, old_token)] = 500`. [2](#0-1) 

After `migrate_deployed_token(origin_chain=Eth, old_token, new_token)`:

- `locked_tokens[(Sol, old_token)]` = 500 (orphaned, never decremented)
- `locked_tokens[(Sol, new_token)]` = **does not exist**

`lock_tokens` and `unlock_tokens` both guard on the key's existence:

```rust
let Some(current_amount) = self.locked_tokens.get(&key) else {
    return LockAction::Unchanged;   // silently no-ops
};
``` [3](#0-2) [4](#0-3) 

Because `locked_tokens[(Sol, new_token)]` is absent, every subsequent call to `lock_tokens_if_needed` or `unlock_tokens_if_needed` for `new_token` on Solana (or any other non-origin chain) returns `LockAction::Unchanged` — silently skipping the accounting update. [5](#0-4) [6](#0-5) 

This affects every code path that calls these helpers:

- `process_fin_transfer_to_near` — `unlock_tokens_if_needed(origin_chain, new_token, amount)` → Unchanged
- `process_fin_transfer_to_other_chain` — `lock_tokens_if_needed(destination_chain, new_token, amount)` → Unchanged [7](#0-6) [8](#0-7) 

### Impact Explanation

The `locked_tokens` map is the bridge's on-chain ledger of how many units of each deployed token are circulating on every non-origin chain. After migration it is permanently zeroed/absent for `new_token`:

1. **Unbounded phantom supply on non-origin chains.** Every `fin_transfer` routing `new_token` to a non-origin chain (e.g. Solana) calls `lock_tokens_if_needed` which silently no-ops. The bridge accumulates no record of tokens sent to Solana, so there is no ceiling on how many can be routed there relative to what was ever burned on NEAR.

2. **Orphaned stale balance for `old_token`.** `locked_tokens[(Sol, old_token)]` retains its pre-migration value indefinitely. Any DAO attempt to read or reason about collateralisation from this map will see a ghost balance for a token that no longer exists in the routing tables.

3. **Broken collateralisation invariant.** The bridge's design relies on `locked_tokens` to ensure that the sum of tokens locked on non-origin chains equals the supply that can be claimed back. After migration this invariant is violated for `new_token` on every non-origin chain.

This matches the allowed impact: **High — balance/accounting corruption that breaks bridge collateralisation.**

### Likelihood Explanation

`migrate_deployed_token` is a DAO-only function intended to be called during legitimate token upgrades (e.g. migrating from a legacy token contract to a new one). The DAO has no reason to suspect the function is incomplete — it updates every other relevant mapping. The bug is triggered automatically the moment the function is called, with no additional attacker action required. Any subsequent cross-chain transfer of `new_token` to or from a non-origin chain silently corrupts the accounting.

### Recommendation

Inside `migrate_deployed_token`, after updating the identity mappings, iterate over all chains that have a `locked_tokens` entry for `old_token` and re-key them to `new_token`. Because `LookupMap` does not support iteration, the simplest approach is to accept an explicit list of `(ChainKind, amount)` pairs as a parameter (matching the existing `set_locked_tokens` pattern) and atomically remove the old entries while inserting the new ones:

```rust
// proposed addition inside migrate_deployed_token
for (chain_kind, amount) in locked_token_entries {
    self.locked_tokens.remove(&(chain_kind, old_token.clone()));
    self.locked_tokens.insert(&(chain_kind, new_token.clone()), &amount);
}
```

Alternatively, expose a separate privileged `migrate_locked_tokens(old_token, new_token, chains)` function that must be called immediately after `migrate_deployed_token`, and document this as a required two-step migration.

### Proof of Concept

**State before migration:**

```
deployed_tokens        = { old_token }
deployed_tokens_v2     = { old_token → Eth }
token_id_to_address    = { (Eth, old_token) → eth_addr, (Sol, old_token) → sol_addr }
token_address_to_id    = { eth_addr → old_token, sol_addr → old_token }
locked_tokens          = { (Eth, old_token) → 0, (Sol, old_token) → 500 }
```

**Call `migrate_deployed_token(Eth, old_token, new_token)`:**

```
deployed_tokens        = { new_token }
deployed_tokens_v2     = { new_token → Eth }
token_id_to_address    = { (Eth, new_token) → eth_addr }   ← Sol entry for old_token gone
token_address_to_id    = { eth_addr → new_token, sol_addr → new_token }
locked_tokens          = { (Eth, old_token) → 0,            ← orphaned
                           (Sol, old_token) → 500 }          ← orphaned
                         // (Sol, new_token) MISSING
```

**Subsequent `fin_transfer` (Solana → NEAR, amount = 300, token = sol_addr → new_token):**

```rust
// process_fin_transfer_to_near:
unlock_tokens_if_needed(Sol, new_token, 300)
  → locked_tokens.get(&(Sol, new_token)) == None
  → return LockAction::Unchanged   // 300 tokens minted on NEAR, no accounting update
``` [9](#0-8) [10](#0-9) [11](#0-10)

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

**File:** near/omni-bridge/src/lib.rs (L1269-1280)
```rust
        require!(
            self.locked_tokens
                .insert(
                    &(
                        deploy_token.token_address.get_chain(),
                        deploy_token.token.clone(),
                    ),
                    &0,
                )
                .is_none(),
            TokenLockError::TokenAlreadyLocked.as_ref()
        );
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

**File:** near/omni-bridge/src/lib.rs (L1881-1885)
```rust
        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];
```

**File:** near/omni-bridge/src/lib.rs (L1997-2022)
```rust
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
```

**File:** near/omni-bridge/src/token_lock.rs (L47-94)
```rust
impl Contract {
    fn lock_tokens(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        let key = (chain_kind, token_id.clone());
        let Some(current_amount) = self.locked_tokens.get(&key) else {
            return LockAction::Unchanged;
        };
        let new_amount = current_amount
            .checked_add(amount)
            .near_expect(TokenLockError::LockedTokensOverflow);

        self.locked_tokens.insert(&key, &new_amount);

        LockAction::Locked {
            chain_kind,
            token_id: token_id.clone(),
            amount,
        }
    }

    fn unlock_tokens(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        let key = (chain_kind, token_id.clone());
        let Some(available) = self.locked_tokens.get(&key) else {
            return LockAction::Unchanged;
        };
        require!(
            available >= amount,
            TokenLockError::InsufficientLockedTokens.as_ref()
        );

        let remaining = available - amount;
        self.locked_tokens.insert(&key, &remaining);

        LockAction::Unlocked {
            chain_kind,
            token_id: token_id.clone(),
            amount,
        }
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L96-107)
```rust
    pub(crate) fn lock_tokens_if_needed(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        if self.get_token_origin_chain(token_id) == chain_kind || amount == 0 {
            return LockAction::Unchanged;
        }

        self.lock_tokens(chain_kind, token_id, amount)
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L109-120)
```rust
    pub(crate) fn unlock_tokens_if_needed(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        if self.get_token_origin_chain(token_id) == chain_kind || amount == 0 {
            return LockAction::Unchanged;
        }

        self.unlock_tokens(chain_kind, token_id, amount)
    }
```
