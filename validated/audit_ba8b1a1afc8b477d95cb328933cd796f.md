### Title
`migrate_deployed_token` Does Not Migrate `locked_tokens` Entries, Breaking Cross-Chain Supply Accounting - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`migrate_deployed_token` replaces `old_token` with `new_token` across all bridge mappings but silently omits migrating the `locked_tokens` entries. After migration, every lock and unlock operation for `new_token` silently returns `LockAction::Unchanged`, permanently breaking the cross-chain supply accounting and bypassing the `ERR_INSUFFICIENT_LOCKED_TOKENS` collateral check for the migrated token.

---

### Finding Description

`migrate_deployed_token` updates `deployed_tokens`, `deployed_tokens_v2`, `token_id_to_address`, `token_address_to_id`, and `migrated_tokens` for the new token, but never touches `locked_tokens`:

```rust
// near/omni-bridge/src/lib.rs  lines 1604-1664
pub fn migrate_deployed_token(
    &mut self,
    origin_chain: ChainKind,
    old_token: AccountId,
    new_token: AccountId,
) {
    // ... updates deployed_tokens, deployed_tokens_v2,
    //     token_id_to_address, token_address_to_id, migrated_tokens
    // ❌ locked_tokens entries for old_token are NEVER migrated to new_token
}
```

`locked_tokens` is a `LookupMap<(ChainKind, AccountId), u128>` that tracks how many tokens are locked on NEAR per destination chain, backing the circulating supply of the bridged token on each foreign chain.

After migration, `locked_tokens[(chain, new_token)]` does not exist for any chain. Both `lock_tokens` and `unlock_tokens` in `token_lock.rs` guard on key existence:

```rust
// near/omni-bridge/src/token_lock.rs  lines 48-94
fn lock_tokens(...) -> LockAction {
    let Some(current_amount) = self.locked_tokens.get(&key) else {
        return LockAction::Unchanged;   // ← silently skips locking
    };
    ...
}

fn unlock_tokens(...) -> LockAction {
    let Some(available) = self.locked_tokens.get(&key) else {
        return LockAction::Unchanged;   // ← silently skips unlock + check
    };
    require!(available >= amount, TokenLockError::InsufficientLockedTokens.as_ref());
    ...
}
```

Because the key is absent for `new_token`, every call to `lock_tokens_if_needed` and `unlock_tokens_if_needed` returns `Unchanged` without performing any accounting or enforcing the collateral check.

This affects every bridge flow that uses `new_token`:

- **`init_transfer_internal`** (line 1853): `lock_tokens_if_needed` is called when a user bridges `new_token` from NEAR to a foreign chain. After migration the lock is silently skipped — the amount is burned on NEAR but never recorded in `locked_tokens`.
- **`process_fin_transfer_to_near`** (line 1881): `unlock_tokens_if_needed` is called when a user finalizes a transfer back to NEAR. After migration the unlock is silently skipped and the `ERR_INSUFFICIENT_LOCKED_TOKENS` check is never enforced.
- **`process_fin_transfer_to_other_chain`** (lines 1997-2005): Both unlock (origin chain) and lock (destination chain) are silently skipped.

Additionally, the stale `locked_tokens[(chain, old_token)]` entries are never removed, leaving permanently inconsistent state.

---

### Impact Explanation

**High — accounting corruption that breaks bridge collateralization.**

The `locked_tokens` invariant is the on-chain record that the NEAR bridge holds sufficient collateral to back every unit of `new_token` circulating on foreign chains. After migration this invariant is silently voided:

1. Tokens burned on NEAR during `init_transfer` are no longer recorded as locked, so the bridge's view of cross-chain supply diverges from reality immediately after the first post-migration transfer.
2. The `ERR_INSUFFICIENT_LOCKED_TOKENS` check — the only on-chain guard preventing the bridge from releasing more tokens than were locked — is bypassed for every finalization involving `new_token`. Any future proof-submission path that reaches `unlock_tokens_if_needed` with `new_token` will succeed regardless of the actual locked balance.
3. The stale `locked_tokens[(chain, old_token)]` entries remain non-zero indefinitely, making the accounting state permanently inconsistent and misleading to any off-chain monitoring or DAO tooling that reads it.

---

### Likelihood Explanation

`migrate_deployed_token` is a DAO-gated function intended to be called in good faith during routine token upgrades (e.g., migrating from a legacy bridge token to a new omni-token contract). The bug is triggered unconditionally by any invocation of this function — no malicious actor is required. Any token migration will silently corrupt the `locked_tokens` accounting for the new token from that point forward.

---

### Recommendation

Inside `migrate_deployed_token`, iterate over all chain kinds that have a `locked_tokens` entry for `old_token` and re-insert them under `new_token`, then remove the stale `old_token` entries. Because `LookupMap` does not support iteration, the caller should supply the list of affected chains, or the function should accept an explicit list:

```rust
pub fn migrate_deployed_token(
    &mut self,
    origin_chain: ChainKind,
    old_token: AccountId,
    new_token: AccountId,
+   locked_token_chains: Vec<ChainKind>,  // chains that have locked_tokens entries
) {
    // ... existing mapping updates ...

+   for chain in locked_token_chains {
+       let key_old = (chain, old_token.clone());
+       if let Some(amount) = self.locked_tokens.get(&key_old) {
+           self.locked_tokens.remove(&key_old);
+           self.locked_tokens.insert(&(chain, new_token.clone()), &amount);
+       }
+   }
}
```

Alternatively, expose a privileged `migrate_locked_tokens(old_token, new_token, chains)` function that can be called immediately after `migrate_deployed_token` to atomically transfer the accounting.

---

### Proof of Concept

**State before migration:**
- `locked_tokens[(ChainKind::Sol, old_token)] = 500` — 500 tokens locked on NEAR backing Solana supply.

**Step 1 — DAO calls `migrate_deployed_token(ChainKind::Eth, old_token, new_token)`:**
- `deployed_tokens`, `deployed_tokens_v2`, address maps updated for `new_token`. ✓
- `locked_tokens[(ChainKind::Sol, old_token)] = 500` — stale, never removed. ✗
- `locked_tokens[(ChainKind::Sol, new_token)]` — does not exist. ✗

**Step 2 — User bridges 500 `new_token` from NEAR to Solana (`init_transfer_internal`):**
- `burn_tokens_if_needed(new_token, 500)` — tokens burned on NEAR. ✓
- `lock_tokens_if_needed(ChainKind::Sol, new_token, 500)` → key absent → `LockAction::Unchanged`. ✗
- `locked_tokens[(ChainKind::Sol, new_token)]` remains absent; bridge has no record of the 500 locked.

**Step 3 — User finalizes a second independent transfer of 500 `new_token` from Solana to NEAR (`process_fin_transfer_to_near`):**
- `unlock_tokens_if_needed(ChainKind::Sol, new_token, 500)` → key absent → `LockAction::Unchanged`. ✗
- The `ERR_INSUFFICIENT_LOCKED_TOKENS` check is never reached.
- 500 `new_token` are minted/transferred to the user on NEAR with no collateral verification.

**Result:** The bridge has burned 500 `new_token` on NEAR (step 2) and minted 500 `new_token` on NEAR (step 3) without any accounting linkage. The stale `locked_tokens[(Sol, old_token)] = 500` entry persists, permanently misrepresenting the bridge's collateral position. The `locked_tokens` invariant — the sole on-chain guard against unbacked supply — is voided for `new_token` for the lifetime of the contract. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** near/omni-bridge/src/lib.rs (L220-242)
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

**File:** near/omni-bridge/src/lib.rs (L1850-1857)
```rust
        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

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

**File:** near/omni-bridge/src/lib.rs (L1997-2006)
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
