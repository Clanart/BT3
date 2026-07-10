### Title
`migrate_deployed_token` Does Not Migrate `locked_tokens` Entries, Breaking Bridge Collateralization Accounting - (`File: near/omni-bridge/src/lib.rs`)

### Summary

The `migrate_deployed_token` function in the NEAR bridge contract updates several token-mapping data structures when migrating from `old_token` to `new_token`, but it does not migrate the `locked_tokens` entries. After migration, all lock/unlock accounting for the new token silently returns `Unchanged`, permanently breaking the bridge's collateralization tracking for that token.

### Finding Description

The `add_token` helper (called during `bind_token_callback` and `deploy_token_internal`) populates three mappings keyed by `OmniAddress` or `(ChainKind, AccountId)`:

- `token_id_to_address: LookupMap<(ChainKind, AccountId), OmniAddress>`
- `token_address_to_id: LookupMap<OmniAddress, AccountId>`
- `token_decimals: LookupMap<OmniAddress, Decimals>`

Additionally, `bind_token_callback` initializes a `locked_tokens` entry keyed by `(ChainKind, AccountId)` (where `AccountId` is the NEAR token id): [1](#0-0) 

The `migrate_deployed_token` function correctly migrates `deployed_tokens`, `deployed_tokens_v2`, `token_id_to_address`, `token_address_to_id`, and `migrated_tokens`: [2](#0-1) 

However, it **never** migrates `locked_tokens`. After migration:
- `locked_tokens` still contains `(origin_chain, old_token) -> amount` (orphaned)
- `locked_tokens` has **no entry** for `(origin_chain, new_token)`

The `lock_tokens` and `unlock_tokens` functions both perform an early-return when no entry exists for the key: [3](#0-2) [4](#0-3) 

Both return `LockAction::Unchanged` silently when `locked_tokens.get(&key)` returns `None`. This means that after migration, every call to `lock_tokens_if_needed` and `unlock_tokens_if_needed` with `new_token` is a no-op — the `available >= amount` guard in `unlock_tokens` is never evaluated. [5](#0-4) 

The `add_token` function (which populates the three address-keyed maps) is called correctly during migration-adjacent flows, but `locked_tokens` is a separate map with a different key scheme (`(ChainKind, AccountId)`) that is never touched by `migrate_deployed_token`: [6](#0-5) 

### Impact Explanation

After `migrate_deployed_token` is called:

1. All subsequent `init_transfer` calls for `new_token` silently skip incrementing the locked amount — the bridge no longer tracks how many tokens are outstanding on the destination chain.
2. All subsequent `fin_transfer`/`claim_fee` calls for `new_token` silently skip the `available >= amount` check — the guard that prevents releasing more tokens than are locked is permanently bypassed for this token.
3. The orphaned `(origin_chain, old_token)` entry in `locked_tokens` is never decremented, permanently inflating the accounting for the old token id.

This constitutes accounting corruption that breaks bridge collateralization: the bridge can no longer enforce that the amount of tokens minted on NEAR does not exceed the amount locked on the origin chain for any migrated token.

**Allowed impact match:** High — Balance/accounting corruption that breaks bridge collateralization.

### Likelihood Explanation

`migrate_deployed_token` is a DAO-callable function explicitly designed for production use (evidenced by the `migrated_tokens` map, `swap_migrated_token` flow, and the `MigrateTokenEvent` log). Any legitimate token migration — e.g., upgrading a deployed token contract — triggers this bug automatically. No malicious operator intent is required; the desync is an inherent consequence of the incomplete migration logic. Once triggered, the broken accounting persists permanently for the new token.

### Recommendation

In `migrate_deployed_token`, after updating all other mappings, also migrate the `locked_tokens` entry:

```rust
// After updating token_address_to_id:
if let Some(locked_amount) = self.locked_tokens.remove(&(origin_chain, old_token.clone())) {
    self.locked_tokens.insert(&(origin_chain, new_token.clone()), &locked_amount);
}
```

This ensures the lock accounting for the new token starts from the correct accumulated value rather than being absent.

### Proof of Concept

1. DAO calls `bind_token` for `old_token` (origin: Ethereum). `bind_token_callback` inserts `locked_tokens[(Eth, old_token)] = 0`.
2. Users bridge tokens: `init_transfer` increments `locked_tokens[(Eth, old_token)]` to, say, `1_000_000`.
3. DAO calls `migrate_deployed_token(ChainKind::Eth, old_token, new_token)`. The function updates `deployed_tokens`, `deployed_tokens_v2`, `token_id_to_address`, `token_address_to_id`, `migrated_tokens` — but **not** `locked_tokens`.
4. State after migration: `locked_tokens[(Eth, old_token)] = 1_000_000` (orphaned), `locked_tokens[(Eth, new_token)]` = **absent**.
5. A user calls `init_transfer` with `new_token`. `lock_tokens_if_needed(Eth, new_token, amount)` calls `lock_tokens`, which calls `self.locked_tokens.get(&(Eth, new_token))` → `None` → returns `LockAction::Unchanged`. No lock is recorded.
6. A relayer calls `fin_transfer`/`claim_fee` for `new_token`. `unlock_tokens_if_needed(Eth, new_token, amount)` similarly returns `Unchanged`. The `available >= amount` invariant is never checked.
7. The bridge has permanently lost collateralization tracking for `new_token`. [7](#0-6) [8](#0-7) [9](#0-8)

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L1606-1664)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L2704-2736)
```rust
    fn add_token(
        &mut self,
        token_id: &AccountId,
        token_address: &OmniAddress,
        decimals: u8,
        origin_decimals: u8,
    ) {
        let chain_kind = token_address.get_chain();
        require!(
            self.token_id_to_address
                .insert(&(chain_kind, token_id.clone()), token_address)
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
        require!(
            self.token_address_to_id
                .insert(token_address, token_id)
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
        require!(
            self.token_decimals
                .insert(
                    token_address,
                    &Decimals {
                        decimals,
                        origin_decimals,
                    }
                )
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L48-68)
```rust
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
```

**File:** near/omni-bridge/src/token_lock.rs (L71-94)
```rust
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

**File:** near/omni-bridge/src/token_lock.rs (L96-120)
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
