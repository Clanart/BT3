### Title
`locked_tokens` Accounting Not Transferred During Token Migration — (`File: near/omni-bridge/src/lib.rs`)

---

### Summary

`migrate_deployed_token` transfers all token-registry mappings from `old_token` to `new_token` but silently drops every `locked_tokens` entry that was keyed on `old_token`. After migration, the new token has no entries in the `locked_tokens` map, so every subsequent `lock_tokens_if_needed` and `unlock_tokens_if_needed` call for the new token silently returns `LockAction::Unchanged` — the solvency check is permanently bypassed for that token.

---

### Finding Description

`migrate_deployed_token` updates five data structures (`deployed_tokens`, `deployed_tokens_v2`, `token_id_to_address`, `token_address_to_id`, `migrated_tokens`) but never touches `locked_tokens`:

```rust
// near/omni-bridge/src/lib.rs  lines 1604-1664
pub fn migrate_deployed_token(
    &mut self,
    origin_chain: ChainKind,
    old_token: AccountId,
    new_token: AccountId,
) {
    // ... registry updates ...
    self.deployed_tokens.remove(&old_token);
    self.deployed_tokens.insert(&new_token);
    self.deployed_tokens_v2.remove(&old_token);
    self.deployed_tokens_v2.insert(&new_token, &origin_chain);
    self.token_id_to_address.remove(&(origin_chain, old_token.clone()));
    self.token_id_to_address.insert(&(origin_chain, new_token.clone()), &origin_address);
    self.token_address_to_id.insert(&origin_address, &new_token);
    self.migrated_tokens.insert(&old_token, &new_token);
    // *** locked_tokens entries for old_token are NEVER copied to new_token ***
}
``` [1](#0-0) 

The `locked_tokens` map is keyed by `(ChainKind, AccountId)` where `AccountId` is the token ID. Both `lock_tokens` and `unlock_tokens` silently return `LockAction::Unchanged` when the key is absent:

```rust
// near/omni-bridge/src/token_lock.rs  lines 48-94
fn lock_tokens(...) -> LockAction {
    let Some(current_amount) = self.locked_tokens.get(&key) else {
        return LockAction::Unchanged;   // ← silent no-op
    };
    ...
}

fn unlock_tokens(...) -> LockAction {
    let Some(available) = self.locked_tokens.get(&key) else {
        return LockAction::Unchanged;   // ← solvency check skipped
    };
    require!(available >= amount, TokenLockError::InsufficientLockedTokens);
    ...
}
``` [2](#0-1) 

After migration, `new_token` has no entries in `locked_tokens`. Every call to `lock_tokens_if_needed` and `unlock_tokens_if_needed` for `new_token` returns `Unchanged`, meaning:

1. Tokens committed to a destination chain are never recorded.
2. The `available >= amount` solvency check is never enforced when unlocking. [3](#0-2) 

---

### Impact Explanation

`locked_tokens` is the on-chain collateralization ledger for bridged tokens. It tracks how many tokens are committed to each non-origin chain and enforces that the bridge cannot unlock more than it has locked. After `migrate_deployed_token`, this invariant is permanently broken for `new_token`:

- `init_transfer_internal` calls `lock_tokens_if_needed` — no tracking occurs.
- `process_fin_transfer_to_near` calls `unlock_tokens_if_needed` — no solvency check occurs.
- `process_fin_transfer_to_other_chain` calls both — both are no-ops. [4](#0-3) [5](#0-4) 

The bridge can finalize inbound transfers for `new_token` from any destination chain without the collateralization check, breaking the accounting invariant that underpins bridge solvency. This matches the allowed impact: **High — balance/accounting corruption that breaks bridge collateralization**.

---

### Likelihood Explanation

`migrate_deployed_token` is a DAO-only operation, but it is a legitimate, expected lifecycle action (token contract upgrades). Once any migration is performed, the broken state is permanent and affects every subsequent unprivileged user interaction (`fin_transfer`, `init_transfer`) involving `new_token`. No further privileged action is required to trigger the broken accounting path. Likelihood: **Medium**.

---

### Recommendation

Inside `migrate_deployed_token`, iterate over all `ChainKind` variants and copy every `locked_tokens` entry from `old_token` to `new_token`, then remove the old entries:

```rust
// After all registry updates in migrate_deployed_token:
for chain_kind in ChainKind::all_variants() {
    let old_key = (chain_kind, old_token.clone());
    if let Some(amount) = self.locked_tokens.get(&old_key) {
        self.locked_tokens.remove(&old_key);
        self.locked_tokens.insert(&(chain_kind, new_token.clone()), &amount);
    }
}
```

This mirrors the recommendation in H-02: transfer all accounting state from the old entity to the new one during the merge/migration operation.

---

### Proof of Concept

1. DAO registers `old_token` as a deployed token bridged from Eth. DAO calls `set_locked_tokens` to initialize `locked_tokens[(Sol, old_token)] = 5000` (5000 tokens committed to Solana).
2. DAO calls `migrate_deployed_token(Eth, old_token, new_token)`. All registry mappings are updated; `locked_tokens[(Sol, old_token)]` remains at 5000 but `locked_tokens[(Sol, new_token)]` is never created.
3. A relayer submits a valid Solana proof for a `new_token` transfer back to NEAR and calls `fin_transfer`. Inside `process_fin_transfer_to_near`, `unlock_tokens_if_needed(Sol, new_token, 5000)` is called. Because `(Sol, new_token)` is absent from the map, it returns `LockAction::Unchanged` — the solvency check `available >= amount` is never evaluated.
4. The bridge mints `new_token` on NEAR with no collateralization enforcement. The `locked_tokens` ledger now permanently misrepresents the bridge's committed obligations, and any number of subsequent finalization calls will continue to bypass the check. [6](#0-5) [7](#0-6)

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L1850-1865)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L1880-1886)
```rust

        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];

```

**File:** near/omni-bridge/src/token_lock.rs (L48-94)
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
