Audit Report

## Title
Unprivileged token-account creation lets any user unboundedly grow another pubkey's `spl_token_owner_index` entry, causing O(n) scans that degrade `getTokenAccountsByOwner`/`getProgramAccounts` for that key - (File: `accounts-db/src/accounts_index/secondary.rs`, `accounts-db/src/accounts_index.rs`, `accounts-db/src/accounts_db.rs`)

## Summary
When a validator/RPC node enables the SPL-token secondary index (`--account-index spl-token-owner`), `AccountsIndex::update_spl_token_secondary_indexes` inserts every new SPL-token account's pubkey into a forward index keyed by that account's self-reported `owner` field via `SecondaryIndex::insert` [1](#0-0) . Because the `owner` field is arbitrary data supplied by the account creator (no signature from the "owner" is required), any unprivileged actor can create unlimited distinct token accounts naming a victim pubkey as owner, growing that pubkey's index entry without bound.

## Finding Description
`SecondaryIndex::get()` returns the entire, uncapped set of inner-key pubkeys stored under an outer key [2](#0-1) , and this feeds `AccountsIndex::get_index_key_pubkeys`, consumed by `AccountsDb::index_scan_accounts`, which performs a full `do_load` for every returned pubkey with no limit on the number of iterations [3](#0-2) . This path backs RPC's `getTokenAccountsByOwner` (via `get_filtered_spl_token_accounts_by_owner` → `get_filtered_indexed_accounts`) and `getProgramAccounts` when the corresponding secondary index is enabled. The insertion path (`SecondaryIndex::insert`) has no cap on how many inner keys a single outer key can accumulate — it only dedupes identical inner-key insertions per outer key via the underlying `HashSet<Pubkey>` in `RwLockSecondaryIndexEntry` [4](#0-3) , which does not help because each attacker-created token account has a distinct pubkey. The `--accounts-index-limit` guard only bounds overall accounts-index *memory*, not the number of entries under a single outer key, so it does not mitigate this growth.

## Impact Explanation
Once a victim pubkey's `spl_token_owner_index` (or `spl_token_mint_index`/`program_id_index`) entry accumulates a large number of attacker-inserted pubkeys, any subsequent `getTokenAccountsByOwner`/`getProgramAccounts` request for that key forces the node to `do_load` every entry in `index_scan_accounts`, scaling CPU/I-O cost with attacker-controlled input. This matches the "single-client low-rate RPC crash/degradation" impact class: an unprivileged, low-rate attacker degrades RPC response latency/resource usage for legitimate lookups against a specific targeted pubkey, without needing peer/validator trust.

## Likelihood Explanation
Exploitability is conditioned on the operator opting into a secondary index (`--account-index spl-token-owner|spl-token-mint|program-id`), which is common for RPC providers needing fast `getTokenAccountsByOwner`/`getProgramAccounts`. No signature or cooperation from the targeted "owner" pubkey is required; the attacker only pays ordinary SPL-token account creation rent and transaction fees, which is cheap and fully repeatable.

## Recommendation
Add a bound on the number of inner-key entries a single outer key (owner/mint/program-id) can accumulate in `SecondaryIndex`, or enforce a page/result-count limit in `get_index_key_pubkeys`/`index_scan_accounts` so a single indexed key cannot force an unbounded `do_load` scan. Consider surfacing a distinct RPC error (analogous to result-too-large errors) when a queried key's indexed entry count exceeds a safe threshold, rather than performing the full scan.

## Proof of Concept
1. Start a validator/RPC node with `--account-index spl-token-owner`.
2. As an unprivileged attacker, repeatedly submit `spl_token::instruction::initialize_account` instructions creating fresh token accounts whose `owner` field is set to a victim pubkey.
3. Each account creation calls `AccountsIndex::update_spl_token_secondary_indexes` → `SecondaryIndex::insert`, appending the new account pubkey to the victim owner's forward-index entry [1](#0-0) .
4. After inserting a large number of such accounts, call `getTokenAccountsByOwner` for the victim's pubkey and observe that `index_scan_accounts` must `do_load` every attacker-inserted pubkey [3](#0-2) , producing latency/resource usage proportional to the number of attacker-created accounts.

### Citations

**File:** accounts-db/src/accounts_index/secondary.rs (L79-111)
```rust
pub struct RwLockSecondaryIndexEntry {
    account_keys: RwLock<HashSet<Pubkey>>,
}

impl SecondaryIndexEntry for RwLockSecondaryIndexEntry {
    fn insert_if_not_exists(&self, key: &Pubkey, inner_keys_count: &AtomicU64) {
        if self.account_keys.read().unwrap().contains(key) {
            // the key already exists, so nothing to do here
            return;
        }

        let was_newly_inserted = self.account_keys.write().unwrap().insert(*key);
        if was_newly_inserted {
            inner_keys_count.fetch_add(1, Ordering::Relaxed);
        }
    }

    fn remove_inner_key(&self, key: &Pubkey) -> bool {
        self.account_keys.write().unwrap().remove(key)
    }

    fn is_empty(&self) -> bool {
        self.account_keys.read().unwrap().is_empty()
    }

    fn keys(&self) -> Vec<Pubkey> {
        self.account_keys.read().unwrap().iter().cloned().collect()
    }

    fn len(&self) -> usize {
        self.account_keys.read().unwrap().len()
    }
}
```

**File:** accounts-db/src/accounts_index/secondary.rs (L133-153)
```rust
    pub fn insert(&self, key: &Pubkey, inner_key: &Pubkey) {
        // Note: Always lock the reverse index first, so we synchronize with remove().
        // Pre-size to 1 to avoid push() over-allocating an empty Vec to capacity 4.
        let reverse_index_entry = self
            .reverse_index
            .entry(*inner_key)
            .or_insert_with(|| RwLock::new(Vec::with_capacity(1)));
        let mut outer_keys = reverse_index_entry.write().unwrap();

        // Now insert into the index.
        // Note, we do this get()-then-unwrap instead of calling entry() directly, because
        // get() is a read lock whereas entry() is a write lock.  We assume `key` already has
        // a map created, so optimize for the common case and only take a read lock.
        self.index
            .get(key)
            .unwrap_or_else(|| self.index.entry(*key).or_default().downgrade())
            .insert_if_not_exists(inner_key, &self.stats.num_inner_keys);

        if !outer_keys.contains(key) {
            outer_keys.push(*key);
        }
```

**File:** accounts-db/src/accounts_index/secondary.rs (L252-258)
```rust
    pub fn get(&self, key: &Pubkey) -> Vec<Pubkey> {
        if let Some(inner_keys_map) = self.index.get(key) {
            inner_keys_map.keys()
        } else {
            vec![]
        }
    }
```

**File:** accounts-db/src/accounts_db.rs (L3398-3410)
```rust
        for pubkey in self.accounts_index.get_index_key_pubkeys(&index_key) {
            if config.is_aborted() {
                break;
            }
            if let Some((account, slot)) = self.do_load(
                ancestors,
                &pubkey,
                LoadHint::Unspecified,
                PopulateReadCache::False,
            ) {
                scan_func(Some((&pubkey, account, slot)));
            }
        }
```
