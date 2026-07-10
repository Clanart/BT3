### Title
`migrate_deployed_token` Does Not Migrate `locked_tokens` Entries, Breaking Bridge Collateralization Accounting - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`migrate_deployed_token` updates all token-mapping state when replacing `old_token` with `new_token`, but silently orphans every `locked_tokens[(chain_kind, old_token)]` entry and creates no corresponding entry for `new_token`. After migration, every `lock_tokens_if_needed` / `unlock_tokens_if_needed` call for `new_token` silently returns `LockAction::Unchanged`, permanently disabling the bridge's collateralization accounting for that token.

---

### Finding Description

`migrate_deployed_token` (DAO-only) updates five data structures:

- `deployed_tokens` (remove old / insert new)
- `deployed_tokens_v2` (remove old / insert new)
- `token_id_to_address` (remove old / insert new)
- `token_address_to_id` (overwrite to point at new)
- `migrated_tokens` (record old → new) [1](#0-0) 

It does **not** touch `locked_tokens`. The `locked_tokens` map is keyed by `(ChainKind, AccountId)` where `AccountId` is the NEAR token account ID. After migration:

- `locked_tokens[(chain_kind, old_token)]` still holds whatever balance was accumulated — it is now orphaned and can never be decremented.
- `locked_tokens[(chain_kind, new_token)]` does not exist.

The `lock_tokens` and `unlock_tokens` helpers both guard with:

```rust
let Some(current_amount) = self.locked_tokens.get(&key) else {
    return LockAction::Unchanged;
};
``` [2](#0-1) [3](#0-2) 

Because no entry exists for `new_token`, every call to `lock_tokens_if_needed` or `unlock_tokens_if_needed` with `new_token` returns `Unchanged` — silently skipping both the `require!(available >= amount, ...)` guard and any state update.

Compare with `bind_token_callback`, which correctly initialises the entry at registration time:

```rust
self.locked_tokens.insert(&(deploy_token.token_address.get_chain(), deploy_token.token.clone()), &0)
``` [4](#0-3) 

`migrate_deployed_token` performs no equivalent initialisation for `new_token`.

---

### Impact Explanation

**High — accounting corruption that breaks bridge collateralization.**

After migration:

1. **`fin_transfer` for `new_token`** — `process_fin_transfer_to_near` calls `unlock_tokens_if_needed(origin_chain, new_token, amount)` which returns `Unchanged`. The `require!(available >= amount)` guard is never executed. Tokens are minted/transferred to the recipient with no decrement of the locked-token counter, removing the secondary over-issuance safeguard entirely. [5](#0-4) 

2. **`init_transfer` for `new_token`** — `init_transfer_internal` calls `lock_tokens_if_needed(destination_chain, new_token, amount)` which also returns `Unchanged`. Outbound transfers are no longer tracked, so the bridge cannot enforce that it holds sufficient collateral for future inbound settlements. [6](#0-5) 

3. **Orphaned counter** — `locked_tokens[(chain_kind, old_token)]` retains its pre-migration balance forever. It can never be decremented because `token_address_to_id` now resolves to `new_token`, so no future `fin_transfer` will ever reference `old_token`.

The net result is that the bridge's on-chain record of how many tokens are locked on each remote chain is permanently corrupted for any migrated token, breaking the invariant that `locked_tokens` ≥ outstanding obligations.

---

### Likelihood Explanation

`migrate_deployed_token` is a DAO operation intended for legitimate token upgrades (e.g., replacing a deployed bridge token contract). Any time the DAO exercises this function — a normal operational event — the broken state is created immediately and persists indefinitely. No special timing, no race condition, and no attacker cooperation is required. Any trusted relayer can then submit a valid `fin_transfer` proof for `new_token` and the collateralization check is silently bypassed.

---

### Recommendation

Inside `migrate_deployed_token`, after updating the token-mapping state, migrate every `locked_tokens` entry from `old_token` to `new_token`. Because a deployed token can have entries for multiple chains (one per chain it has been bridged to/from), iterate over all `ChainKind` variants and transfer each entry:

```rust
for chain_kind in ChainKind::all() {
    let old_key = (chain_kind, old_token.clone());
    if let Some(amount) = self.locked_tokens.remove(&old_key) {
        self.locked_tokens.insert(&(chain_kind, new_token.clone()), &amount);
    }
}
```

Alternatively, at minimum, initialise `locked_tokens[(origin_chain, new_token)] = locked_tokens[(origin_chain, old_token)]` and remove the old entry, mirroring what `bind_token_callback` does at registration.

---

### Proof of Concept

**State before migration:**

```
deployed_tokens: { old_token }
token_id_to_address: { (Eth, old_token) → 0xABC }
token_address_to_id: { 0xABC → old_token }
locked_tokens: { (Eth, old_token) → 1_000_000 }
```

**DAO calls `migrate_deployed_token(ChainKind::Eth, old_token, new_token)`:**

```
deployed_tokens: { new_token }
token_id_to_address: { (Eth, new_token) → 0xABC }
token_address_to_id: { 0xABC → new_token }
migrated_tokens: { old_token → new_token }
locked_tokens: { (Eth, old_token) → 1_000_000 }   ← orphaned, never decremented
                                                    ← (Eth, new_token) does NOT exist
```

**Trusted relayer submits `fin_transfer` with a valid ETH proof for `new_token`, amount = 500_000:**

1. `get_token_id(0xABC)` → `new_token` ✓
2. `unlock_tokens_if_needed(Eth, new_token, 500_000)`:
   - `locked_tokens.get(&(Eth, new_token))` → `None`
   - returns `LockAction::Unchanged` — **no check, no decrement**
3. `send_tokens(new_token, recipient, 500_000)` executes — tokens delivered.

**Result:** 500_000 tokens released to recipient; `locked_tokens[(Eth, old_token)]` remains 1_000_000 (orphaned); `locked_tokens[(Eth, new_token)]` still does not exist. The bridge's collateralization accounting is permanently broken for this token.

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

**File:** near/omni-bridge/src/lib.rs (L1617-1655)
```rust
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

**File:** near/omni-bridge/src/token_lock.rs (L77-80)
```rust
        let key = (chain_kind, token_id.clone());
        let Some(available) = self.locked_tokens.get(&key) else {
            return LockAction::Unchanged;
        };
```
