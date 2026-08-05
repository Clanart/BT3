Confirmed: `--accounts-index-scan-results-limit-mb` has **no default value** — it's an optional clap arg with no `default_value()`, meaning `scan_results_limit_bytes` is `None` unless an operator explicitly sets it. This is the critical gap: the only protection against an unbounded `SecondaryIndex` scan is opt-in and off by default.

### Title
Unbounded, permissionlessly-inflatable secondary-index entries allow single-key `getProgramAccounts`/`getTokenAccountsByOwner` scans to exhaust RPC resources - ([File: accounts-db/src/accounts_index/secondary.rs])

### Summary
The `SecondaryIndex` structure that backs `AccountIndex::ProgramId` / `SplTokenOwner` / `SplTokenMint` stores, for every indexed key, an unbounded `Vec<Pubkey>`/`HashSet<Pubkey>` of every account whose `owner`/`mint`/`token-owner` field matches that key. Any unprivileged client can permissionlessly create arbitrarily many accounts (paying only rent) whose attacker-controlled `owner`/`mint`/`SPL owner` field targets a single victim key (e.g. a popular program id or a victim wallet's SPL-token-owner key), inflating that one index bucket to millions of entries. When any client (or the victim) later queries that key via `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByMint`, the RPC node must materialize and iterate the full, unbounded pubkey list before any size-based abort can trigger, and the only mitigation (`accounts-index-scan-results-limit-mb`) is off by default.

### Finding Description
`SecondaryIndex::insert` pushes into `self.index` (`DashMap<Pubkey, SecondaryIndexEntryType>`) and `self.reverse_index` (`DashMap<Pubkey, RwLock<Vec<Pubkey>>>`) with no cap on how many inner keys (accounts) a single outer key (program id / mint / owner) can accumulate: [1](#0-0) 

This index is populated automatically whenever an account is stored and its owner matches a token program, via `update_spl_token_secondary_indexes`, which unpacks the attacker-controlled `owner`/`mint` bytes straight out of account data with no access control on who can register under a given outer key: [2](#0-1) 

This is the direct analog of the report's `registerVault`/`vaults[owner].push(vault)` bug: instead of a factory-gated registration, any address can be pushed into a shared, victim-keyed collection with no cap and no owner-side consent, because vault registration (there) / index insertion (here) has no access control tying insertion rights to the target key's owner.

When the index is queried, `index_scan_accounts` retrieves the *entire* list of pubkeys for the target key and iterates them one at a time, calling `do_load` for every single pubkey before any scan-abort check can short-circuit the loop: [3](#0-2) 

The only defense against unbounded accumulated results is the byte-limit check performed in `load_by_index_key_with_filter`/`accumulate_and_check_scan_result_size`, which only aborts the scan *after* accumulating enough matching bytes to exceed `byte_limit_for_scan`: [4](#0-3) [5](#0-4) 

Crucially, that limit is `scan_results_limit_bytes`, which is wired from the CLI flag `--accounts-index-scan-results-limit-mb`. That flag has no `default_value(...)` set, so unless an operator explicitly configures it, `scan_results_limit_bytes` is `None` and the abort logic in `accumulate_and_check_scan_result_size` never fires at all: [6](#0-5) [7](#0-6) 

The corrupted value is the per-key entry count inside `SecondaryIndex::index`/`reverse_index` for whichever outer key (program id, SPL mint, or SPL owner) the attacker targets — it can be inflated arbitrarily by any unprivileged account creator, with no relationship required between the attacker and the targeted key's owner.

### Impact Explanation
Any RPC node that has enabled secondary indexes (`--account-index program-id`/`spl-token-owner`/`spl-token-mint`, common on indexer/exchange infrastructure) and has not separately set `--accounts-index-scan-results-limit-mb` can be driven into pathological CPU/IO usage by a single low-cost attacker: create a large number of cheap accounts whose owner/mint/token-owner field points at one victim key, then trigger (or wait for a legitimate user/service to trigger) a `getProgramAccounts`/`getTokenAccountsByOwner`/`getTokenAccountsByMint` query on that key. The scan must walk and `do_load` every entry in that bucket before any size check can trip, degrading or crashing the RPC service for that single client/key — this falls under "single-client low-rate RPC crash/degradation" and "non-RPC remote exhaustion" risk categories since account creation that inflates the index is itself a normal transaction, not an RPC call, so the resource exhaustion is triggered off-RPC and only manifests during a later RPC/internal scan (e.g. `calculate_non_circulating_supply`, which uses `IndexKey::ProgramId(stake::program::id())` and runs on the hot supply-calculation path): [8](#0-7) 

### Likelihood Explanation
High for any deployment that turns on secondary indexes without also setting the non-default scan-result byte limit. Creating rent-paying accounts with an attacker-chosen owner/mint field is a completely permissionless, low-cost, everyday operation (this is literally how SPL token accounts and program-owned accounts normally work), so no privileged role, leaked key, or malicious-validator assumption is required — matching the "unprivileged" and "non-RPC remote exhaustion" criteria.

### Recommendation
- Set a sane non-zero default for `accounts-index-scan-results-limit-mb` rather than leaving it unset/unbounded.
- Enforce a hard cap on the number of entries a single outer key can accumulate in `SecondaryIndex`, independent of scan-time byte limits, and/or check the byte/entry limit before iterating the full `get_index_key_pubkeys` list rather than only after loading each account.
- Consider rate/size limiting or emitting metrics-driven alerts when a single secondary-index key grows abnormally large (`SecondaryIndex::log_contents` already tracks top offenders, but only for manual inspection, not enforcement): [9](#0-8) 

### Proof of Concept
1. Run a validator/RPC node with `--account-index spl-token-owner` (or `program-id`) enabled and without `--accounts-index-scan-results-limit-mb`.
2. Submit a large volume of cheap `CreateAccount` + SPL Token `InitializeAccount` transactions, each setting the token account's `owner` field to the same victim pubkey `V` (this is permitted; `owner` is just account data the creator writes, unrelated to who signs the creation transaction). Each insertion goes through `update_spl_token_secondary_indexes` → `SecondaryIndex::insert`, unconditionally growing `reverse_index[V]`/`index[token_program][V]`. [2](#0-1) 
3. Once the bucket for `V` contains a very large number of entries, call `getTokenAccountsByOwner(V, ...)` (or have any dependent internal scan such as `calculate_non_circulating_supply` run against a similarly inflated `ProgramId` bucket).
4. `index_scan_accounts` retrieves and iterates the full pubkey list for `V`, invoking `do_load` for every entry before the (disabled-by-default) byte-limit abort can trigger, degrading RPC responsiveness / consuming excessive I/O for that request. [3](#0-2)

### Citations

**File:** accounts-db/src/accounts_index/secondary.rs (L132-153)
```rust
    /// Inserts `inner_key` into `key`'s map.
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

**File:** accounts-db/src/accounts_index/secondary.rs (L260-273)
```rust
    /// log top 20 (owner, # accounts) in descending order of # accounts
    pub fn log_contents(&self) {
        let mut entries = self
            .index
            .iter()
            .map(|entry| (entry.value().len(), *entry.key()))
            .collect::<Vec<_>>();
        entries.sort_unstable();
        entries
            .iter()
            .rev()
            .take(20)
            .for_each(|(v, k)| info!("owner: {k}, accounts: {v}"));
    }
```

**File:** accounts-db/src/accounts_index.rs (L557-580)
```rust
    fn update_spl_token_secondary_indexes<G: spl_generic_token::token::GenericTokenAccount>(
        &self,
        token_id: &Pubkey,
        pubkey: &Pubkey,
        account_owner: &Pubkey,
        account_data: &[u8],
        account_indexes: &AccountSecondaryIndexes,
    ) {
        if *account_owner == *token_id {
            if account_indexes.contains(&AccountIndex::SplTokenOwner)
                && let Some(owner_key) = G::unpack_account_owner(account_data)
                && account_indexes.include_key(owner_key)
            {
                self.spl_token_owner_index.insert(owner_key, pubkey);
            }

            if account_indexes.contains(&AccountIndex::SplTokenMint)
                && let Some(mint_key) = G::unpack_account_mint(account_data)
                && account_indexes.include_key(mint_key)
            {
                self.spl_token_mint_index.insert(mint_key, pubkey);
            }
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

**File:** accounts-db/src/accounts.rs (L367-381)
```rust
    /// Return true iff sum > 'byte_limit_for_scan'
    fn accumulate_and_check_scan_result_size(
        sum: &AtomicUsize,
        account: &AccountSharedData,
        byte_limit_for_scan: &Option<usize>,
    ) -> bool {
        if let Some(byte_limit_for_scan) = byte_limit_for_scan.as_ref() {
            let added = Self::calc_scan_result_size(account);
            sum.fetch_add(added, Ordering::Relaxed)
                .saturating_add(added)
                > *byte_limit_for_scan
        } else {
            false
        }
    }
```

**File:** accounts-db/src/accounts.rs (L396-433)
```rust
    pub fn load_by_index_key_with_filter<F: Fn(&AccountSharedData) -> bool>(
        &self,
        ancestors: &Ancestors,
        bank_id: BankId,
        index_key: &IndexKey,
        filter: F,
        byte_limit_for_scan: Option<usize>,
    ) -> ScanResult<Vec<KeyedAccountSharedData>> {
        let sum = AtomicUsize::default();
        let config = ScanConfig::default().recreate_with_abort();
        let mut collector = Vec::new();
        let result = self
            .accounts_db
            .index_scan_accounts(
                ancestors,
                bank_id,
                *index_key,
                |some_account_tuple| {
                    Self::load_while_filtering(&mut collector, some_account_tuple, |account| {
                        let use_account = filter(account);
                        if use_account
                            && Self::accumulate_and_check_scan_result_size(
                                &sum,
                                account,
                                &byte_limit_for_scan,
                            )
                        {
                            // total size of results exceeds size limit, so abort scan
                            config.abort();
                        }
                        use_account
                    });
                },
                &config,
            )
            .map(|_| collector);
        Self::maybe_abort_scan(result, &config)
    }
```

**File:** validator/src/commands/run/args/json_rpc_config.rs (L58-65)
```rust
            scan_results_limit_bytes: value_t!(
                matches,
                "accounts_index_scan_results_limit_mb",
                usize
            )
            .ok()
            .map(|mb| mb * MB),
            disable_health_check: false,
```

**File:** validator/src/commands/run/args/json_rpc_config.rs (L179-187)
```rust
        Arg::with_name("accounts_index_scan_results_limit_mb")
            .long("accounts-index-scan-results-limit-mb")
            .value_name("MEGABYTES")
            .validator(is_parsable::<usize>)
            .takes_value(true)
            .help(
                "How large accumulated results from an accounts index scan can become. If this is \
                 exceeded, the scan aborts.",
            ),
```

**File:** runtime/src/non_circulating_supply.rs (L19-47)
```rust
pub fn calculate_non_circulating_supply(bank: &Bank) -> ScanResult<NonCirculatingSupply> {
    debug!("Updating Bank supply, epoch: {}", bank.epoch());
    let mut non_circulating_accounts_set: HashSet<Pubkey> = HashSet::new();

    for key in non_circulating_accounts() {
        non_circulating_accounts_set.insert(key);
    }
    let withdraw_authority_list = withdraw_authority();

    let clock = bank.clock();
    let stake_accounts = if bank
        .rc
        .accounts
        .accounts_db
        .account_indexes
        .contains(&AccountIndex::ProgramId)
    {
        bank.get_filtered_indexed_accounts(
            &IndexKey::ProgramId(stake::program::id()),
            // The program-id account index checks for Account owner on inclusion. However, due to
            // the current AccountsDb implementation, an account may remain in storage as a
            // zero-lamport Account::Default() after being wiped and reinitialized in later
            // updates. We include the redundant filter here to avoid returning these accounts.
            |account| account.owner() == &stake::program::id(),
            None,
        )?
    } else {
        bank.get_program_accounts(&stake::program::id())?
    };
```
