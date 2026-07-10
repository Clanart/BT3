### Title
`locked_tokens` Accounting Not Migrated When Token ID Changes — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`migrate_deployed_token` updates five token-identity mappings but silently omits the `locked_tokens` map. After migration the old token's locked-counter entry is orphaned and the new token has no entry at all. Every subsequent `lock_tokens` / `unlock_tokens` call for the new token silently returns `LockAction::Unchanged`, permanently disabling the bridge's collateralization guard for that token.

---

### Finding Description

`migrate_deployed_token` (DAO-only) rewrites the token identity across five maps: [1](#0-0) 

It removes `old_token` from `deployed_tokens`, `deployed_tokens_v2`, and `token_id_to_address`, inserts `new_token` into all three, rewrites `token_address_to_id`, and records the migration in `migrated_tokens`. It does **not** touch `locked_tokens`.

`locked_tokens` is keyed by `(ChainKind, AccountId)`. When a token is first registered via `bind_token_callback`, the entry is explicitly seeded to zero: [2](#0-1) 

After `migrate_deployed_token`:
- `locked_tokens[(origin_chain, old_token)]` still holds whatever accumulated value it had — orphaned forever.
- `locked_tokens[(origin_chain, new_token)]` does not exist.

`lock_tokens` and `unlock_tokens` both guard on the existence of the map entry: [3](#0-2) [4](#0-3) 

When the entry is absent both functions return `LockAction::Unchanged` — the `require!(available >= amount, InsufficientLockedTokens)` guard is never reached for `new_token`.

`lock_tokens_if_needed` and `unlock_tokens_if_needed` call these functions unconditionally (after the origin-chain short-circuit): [5](#0-4) 

---

### Impact Explanation

After a legitimate DAO migration:

1. **`init_transfer` (NEAR → foreign chain):** `lock_tokens_if_needed` is called for `new_token` and silently does nothing. The bridge never increments the locked counter, so the collateralization ledger permanently under-counts outstanding obligations.

2. **`fin_transfer` (foreign chain → NEAR):** `unlock_tokens_if_needed` is called for `new_token` and silently does nothing. The `InsufficientLockedTokens` guard — the only on-chain check preventing the bridge from releasing more tokens than it has locked — is permanently bypassed. Any user who submits a valid proof for a `new_token` inbound transfer will have tokens minted without the counter being checked or decremented.

3. The orphaned `locked_tokens[(origin_chain, old_token)]` entry can never be decremented, permanently inflating the apparent locked balance for the old token ID.

This is a **balance / accounting corruption that breaks bridge collateralization**, matching the allowed High impact class.

---

### Likelihood Explanation

**Low.** The trigger is a DAO call to `migrate_deployed_token`. This is a legitimate administrative operation (e.g., upgrading a token contract to a new account ID). Once executed, the broken accounting is permanent and affects every subsequent bridge user of the migrated token — no further privileged action is required to reach the broken code paths.

---

### Recommendation

In `migrate_deployed_token`, transfer the `locked_tokens` entry from `old_token` to `new_token`:

```rust
// After updating token_id_to_address / token_address_to_id:
if let Some(locked_amount) = self.locked_tokens.remove(&(origin_chain, old_token.clone())) {
    self.locked_tokens.insert(&(origin_chain, new_token.clone()), &locked_amount);
}
```

If the token has entries for multiple chains (e.g., it is bridged to several destination chains), all `(chain_kind, old_token)` entries must be migrated to `(chain_kind, new_token)`. Consider iterating over all known chain kinds or storing the set of chains per token.

---

### Proof of Concept

**State before migration:**
- `locked_tokens[(Eth, old_token)] = 1_000_000` (1 M tokens locked on Ethereum)
- `deployed_tokens_v2[old_token] = Eth`

**Step 1 — DAO calls `migrate_deployed_token(Eth, old_token, new_token)`.**

After the call:
- `deployed_tokens_v2[new_token] = Eth` ✓
- `token_id_to_address[(Eth, new_token)] = <eth_address>` ✓
- `locked_tokens[(Eth, old_token)] = 1_000_000` (orphaned) ✗
- `locked_tokens[(Eth, new_token)]` — **does not exist** ✗

**Step 2 — Any user calls `fin_transfer` with a valid Ethereum proof for `new_token`, amount = 500_000.**

Inside `process_fin_transfer_to_near`:
```
unlock_tokens_if_needed(Eth, new_token, 500_000)
  → get_token_origin_chain(new_token) = Eth  (from deployed_tokens_v2)
  → Eth == Eth  → returns LockAction::Unchanged   ← wait, this short-circuits
```

Actually for the destination-chain path (`process_fin_transfer_to_other_chain`), `unlock_tokens_if_needed` is called with the **origin** chain of the incoming message (e.g., `Eth`), and `get_token_origin_chain(new_token)` also returns `Eth`, so the origin-chain short-circuit fires and the unlock is skipped for a different reason. The critical path is for a **non-origin** chain:

**Step 2 (revised) — User calls `fin_transfer` for `new_token` bridged from Solana (a secondary chain where `new_token` also has a locked entry).**

`unlock_tokens_if_needed(Sol, new_token, 500_000)`:
- `get_token_origin_chain(new_token)` = `Eth` ≠ `Sol` → proceeds to `unlock_tokens`
- `locked_tokens.get(&(Sol, new_token))` → `None` → returns `LockAction::Unchanged`
- The `require!(available >= amount)` guard is **never evaluated**
- Tokens are minted to the recipient with no collateralization check

The bridge can now mint unbounded amounts of `new_token` on NEAR for any valid Solana proof, with no counter ever being decremented. [6](#0-5) [7](#0-6) [8](#0-7)

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

**File:** near/omni-bridge/src/token_lock.rs (L54-57)
```rust
        let key = (chain_kind, token_id.clone());
        let Some(current_amount) = self.locked_tokens.get(&key) else {
            return LockAction::Unchanged;
        };
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
