### Title
`migrate_deployed_token` Does Not Migrate `locked_tokens` Entries, Causing Permanent Collateralization Accounting Corruption for the New Token - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`migrate_deployed_token` updates every token mapping (`deployed_tokens`, `deployed_tokens_v2`, `token_id_to_address`, `token_address_to_id`, `migrated_tokens`) but silently omits the `locked_tokens` map. After migration, all `lock_tokens_if_needed` / `unlock_tokens_if_needed` calls for `new_token` silently return `LockAction::Unchanged` because the key `(chain_kind, new_token)` does not exist. The old entries under `(chain_kind, old_token)` are permanently orphaned. Every subsequent `fin_transfer` and `init_transfer` for the migrated token bypasses the collateralization check entirely.

---

### Finding Description

`migrate_deployed_token` is a DAO-callable function that replaces `old_token` with `new_token` across all bridge state: [1](#0-0) 

It removes `old_token` from `deployed_tokens` and `deployed_tokens_v2`, rewrites `token_id_to_address` and `token_address_to_id`, and records the migration in `migrated_tokens`. It does **not** touch `locked_tokens`.

The `locked_tokens` map is keyed by `(ChainKind, AccountId)` where `AccountId` is the NEAR token ID. It is initialized to `0` during `bind_token_callback`: [2](#0-1) 

After `migrate_deployed_token(origin_chain, old_token, new_token)`:

- `locked_tokens[(chain, old_token)]` still holds the pre-migration balance — permanently orphaned, unreachable by any future operation.
- `locked_tokens[(chain, new_token)]` does not exist.

`lock_tokens` and `unlock_tokens` both guard on key existence with an early-return of `LockAction::Unchanged`: [3](#0-2) [4](#0-3) 

The `unlock_tokens` path is critical: when the key is absent it returns `Unchanged` **without** executing the `available >= amount` guard. This means the collateralization check is silently skipped for every `fin_transfer` involving `new_token`.

`process_fin_transfer_to_near` calls `unlock_tokens_if_needed` and stores the result in `lock_actions`: [5](#0-4) 

`init_transfer_internal` calls `lock_tokens_if_needed`: [6](#0-5) 

Both calls silently return `Unchanged` for `new_token` because the key was never inserted.

---

### Impact Explanation

**High — Balance/accounting corruption that breaks bridge collateralization.**

1. **Orphaned locked balance**: The pre-migration `locked_tokens[(chain, old_token)]` amount can never be decremented. It represents tokens that are permanently "phantom-locked" — the accounting says they are committed to cross-chain transfers, but no future operation can release them.

2. **Collateralization check bypass on `fin_transfer`**: For every incoming transfer finalized for `new_token`, `unlock_tokens_if_needed` returns `Unchanged`. The `available >= amount` guard in `unlock_tokens` is never reached. The bridge releases tokens to recipients without verifying that a corresponding locked balance exists, breaking the invariant that released tokens must have been locked by a prior `init_transfer`.

3. **Silent locking failure on `init_transfer`**: Outgoing transfers for `new_token` do not increment any locked balance. The bridge cannot track how many tokens are committed to other chains, making the collateralization ledger permanently inaccurate.

The combination means the bridge's internal accounting of cross-chain token commitments is irrecoverably corrupted for the migrated token after a single DAO call.

---

### Likelihood Explanation

**Medium.** `migrate_deployed_token` is a legitimate operational function — it exists precisely to replace a deployed token contract. Any time the DAO exercises this function (e.g., upgrading a token implementation), the accounting corruption is introduced silently. No malicious intent is required. After migration, every ordinary user who calls `fin_transfer` or `init_transfer` for `new_token` exercises the broken path.

---

### Recommendation

Inside `migrate_deployed_token`, iterate over all `ChainKind` variants and migrate each `locked_tokens` entry from `old_token` to `new_token`:

```rust
// For each chain kind that has a locked_tokens entry for old_token,
// move it to new_token and remove the old entry.
for chain_kind in ChainKind::all_variants() {
    let old_key = (chain_kind, old_token.clone());
    if let Some(amount) = self.locked_tokens.remove(&old_key) {
        self.locked_tokens.insert(&(chain_kind, new_token.clone()), &amount);
    }
}
```

Alternatively, add an explicit `set_locked_tokens` call (already available via `Role::TokenLockController`) as a required post-migration step, and document it as mandatory. The safer approach is to perform the migration atomically inside `migrate_deployed_token` itself so the state is never left inconsistent.

---

### Proof of Concept

**Setup** (analogous to the MarinateV2 scenario):

1. Token `old.near` is registered via `bind_token_callback` for `ChainKind::Eth`. This initializes `locked_tokens[(Eth, old.near)] = 0`.
2. Users bridge tokens: `init_transfer` for `old.near` → Solana increments `locked_tokens[(Sol, old.near)] = 1_000_000`.
3. DAO calls `migrate_deployed_token(Eth, old.near, new.near)`.
   - `deployed_tokens`, `deployed_tokens_v2`, `token_id_to_address`, `token_address_to_id` are all updated to `new.near`.
   - `locked_tokens[(Sol, old.near)] = 1_000_000` remains — orphaned.
   - `locked_tokens[(Sol, new.near)]` does not exist.

**Exploit** (any user, post-migration):

4. A relayer submits `fin_transfer` with a valid proof for `new.near` from Solana (amount = 1_000_000).
5. `process_fin_transfer_to_near` calls `unlock_tokens_if_needed(Sol, new.near, 1_000_000)`.
6. `get_token_origin_chain(new.near)` returns `Eth` (from `deployed_tokens_v2`). `Eth ≠ Sol`, so `unlock_tokens(Sol, new.near, 1_000_000)` is called.
7. `locked_tokens.get(&(Sol, new.near))` returns `None` → `LockAction::Unchanged` is returned immediately. The `available >= amount` check is **never executed**.
8. The bridge sends 1_000_000 `new.near` tokens to the recipient.
9. `locked_tokens[(Sol, old.near)]` still reads `1_000_000` — permanently stuck. `locked_tokens[(Sol, new.near)]` still does not exist.

**Result**: The bridge released 1_000_000 tokens without decrementing any locked balance. The orphaned `locked_tokens[(Sol, old.near)]` can never be settled. All future `init_transfer` / `fin_transfer` calls for `new.near` operate with no collateralization tracking. [7](#0-6) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1270-1280)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L1853-1857)
```rust
            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
```

**File:** near/omni-bridge/src/lib.rs (L1881-1885)
```rust
        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];
```

**File:** near/omni-bridge/src/token_lock.rs (L54-57)
```rust
        let key = (chain_kind, token_id.clone());
        let Some(current_amount) = self.locked_tokens.get(&key) else {
            return LockAction::Unchanged;
        };
```

**File:** near/omni-bridge/src/token_lock.rs (L77-84)
```rust
        let key = (chain_kind, token_id.clone());
        let Some(available) = self.locked_tokens.get(&key) else {
            return LockAction::Unchanged;
        };
        require!(
            available >= amount,
            TokenLockError::InsufficientLockedTokens.as_ref()
        );
```
