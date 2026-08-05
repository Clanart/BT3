## Agave Analog Found

The dMute bug's core primitive — **an unprivileged actor can append entries to a data structure keyed by an arbitrary victim pubkey, and that unbounded structure is later iterated in bulk by a function whose cost scales with the number of entries** — has a direct analog in Agave's SPL-token secondary index.

### Title
Unprivileged token-account creation lets any user unboundedly grow another pubkey's `spl_token_owner_index` entry, causing O(n) scans that degrade `getTokenAccountsByOwner`/`getProgramAccounts` for that key - (File: `accounts-db/src/accounts_index/secondary.rs`, `accounts-db/src/accounts_index.rs`)

### Summary
When the `--account-index spl-token-owner` (or `spl-token-mint`/`program-id`) secondary index is enabled, `AccountsIndex::update_spl_token_secondary_indexes` inserts every new SPL-token account's `pubkey` into a reverse index keyed by the account's *self-reported* `owner` field [1](#0-0) . Anyone can create an arbitrary number of token accounts and set the `owner` field to any pubkey they choose — the "owner" of a token account is just data written by the creator, not a signer that must consent. This is exactly analogous to `dMute::LockTo` letting anyone push a `UserLockInfo` entry into any victim address's array.

### Finding Description
The reverse-index entry for a given key is an unbounded `Vec<Pubkey>` (`SecondaryReverseIndexEntry`) [2](#0-1) , and `SecondaryIndex::get()` returns the *entire* list of pubkeys ever inserted for that key with no cap [3](#0-2) . This list is consumed by `AccountsDb::index_scan_accounts`, which does a `do_load` (a full account fetch) for *every* pubkey in the list [4](#0-3) . That path is reached by RPC's `getFilteredIndexedAccounts` used by `get_filtered_spl_token_accounts_by_owner` (backing `getTokenAccountsByOwner`) and `get_filtered_program_accounts` (backing `getProgramAccounts`) [5](#0-4) .

Unlike the accounts index memory limiter (`--accounts-index-limit`, which bounds total index *memory*, not per-key entry counts) [6](#0-5) , there is no guard limiting how many entries a single outer key (`ProgramId`/`SplTokenMint`/`SplTokenOwner`) can accumulate. An attacker who repeatedly issues cheap `InitializeAccount` token instructions, setting the `owner` field to a victim's pubkey (e.g., an exchange hot wallet, a well-known DAO treasury, or any RPC-heavy address), can grow that pubkey's reverse-index `Vec<Pubkey>` without bound — mirroring how the dMute attacker grows the victim's `UserLockInfo[]` without the victim's participation.

### Impact Explanation
Once the victim's index entry contains a large number of pubkeys, any subsequent `getTokenAccountsByOwner`/`getProgramAccounts` call for that key forces the RPC node to iterate and `do_load` every attacker-inserted pubkey, causing CPU/I-O amplification proportional to attacker-controlled input. This fits the "single-client low-rate RPC crash/degradation" impact class: a single low-rate attacker (issuing ordinary token-account creation transactions, no elevated privilege, no validator/peer trust) degrades RPC service quality/latency for legitimate lookups against that specific pubkey, and at sufficient scale can exhaust node time/memory on that scan. This is directly analogous to `RedeemTo`/`GetUnderlyingTokens` becoming unusable in the dMute report.

### Likelihood Explanation
Likelihood is real but conditioned on the operator having opted into the secondary index (`--account-index spl-token-owner|spl-token-mint|program-id`), which is common among RPC providers that need fast `getTokenAccountsByOwner`/`getProgramAccounts` responses [7](#0-6) . No signature from the targeted "owner" is required to add entries, and the cost to the attacker is just SPL-token account rent plus transaction fees — extremely cheap, matching the negligible-cost profile of the original PoC.

### Recommendation
Cap the number of entries the reverse (`inner_keys`) index can accumulate per outer key, or evict/bound entries similarly to how the primary index enforces `IndexLimitThreshold`. Alternatively, add a per-key page/result-count limit to `get_index_key_pubkeys`/`index_scan_accounts` so a single indexed key cannot force an unbounded `do_load` scan, and consider rate-limiting or additional validation when the number of distinct accounts pointing to a single "owner" grows abnormally large.

### Proof of Concept
1. Start a validator/RPC node with `--account-index spl-token-owner`.
2. As an unprivileged attacker, repeatedly submit `spl_token::instruction::initialize_account` (or account_v3) instructions creating fresh token accounts whose `owner` field is set to a victim pubkey (no signature from the victim is required for this field).
3. Each such account creation calls `AccountsDb::update_spl_token_secondary_indexes`, which appends the new account's pubkey to `spl_token_owner_index`'s reverse-index entry for the victim key [8](#0-7) .
4. After inserting a large number (analogous to the 5000 lock entries in the original PoC), call `getTokenAccountsByOwner` for the victim's pubkey; observe that `get_filtered_spl_token_accounts_by_owner` → `get_filtered_indexed_accounts` → `index_scan_accounts` must `do_load` every attacker-inserted pubkey [4](#0-3) , resulting in dramatically increased latency/resource usage compared to before the attack.

### Citations

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

**File:** accounts-db/src/accounts_index/secondary.rs (L57-61)
```rust
// The only cases where an inner key should map to a different outer key is
// if the key had different account data for the indexed key across different
// slots. As this is rare, it should be ok to use a Vec here over a HashSet, even
// though we are running some key existence checks.
type SecondaryReverseIndexEntry = RwLock<Vec<Pubkey>>;
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

**File:** rpc/src/rpc.rs (L2310-2357)
```rust
    /// Get an iterator of spl-token accounts by owner address
    async fn get_filtered_spl_token_accounts_by_owner(
        &self,
        bank: Arc<Bank>,
        program_id: Pubkey,
        owner_key: Pubkey,
        mut filters: Vec<RpcFilterType>,
        sort_results: bool,
    ) -> RpcCustomResult<Vec<(Pubkey, AccountSharedData)>> {
        // The by-owner accounts index checks for Token Account state and Owner address on
        // inclusion. However, due to the current AccountsDb implementation, an account may remain
        // in storage as a zero-lamport AccountSharedData::Default() after being wiped and reinitialized in
        // later updates. We include the redundant filters here to avoid returning these accounts.
        //
        // Filter on Token Account state
        filters.push(RpcFilterType::TokenAccountState);
        // Filter on Owner address
        filters.push(RpcFilterType::Memcmp(Memcmp::new_raw_bytes(
            SPL_TOKEN_ACCOUNT_OWNER_OFFSET,
            owner_key.to_bytes().into(),
        )));

        if self
            .config
            .account_indexes
            .contains(&AccountIndex::SplTokenOwner)
        {
            if !self.config.account_indexes.include_key(&owner_key) {
                return Err(RpcCustomError::KeyExcludedFromSecondaryIndex {
                    index_key: owner_key.to_string(),
                });
            }
            self.get_filtered_indexed_accounts(
                &bank,
                &IndexKey::SplTokenOwner(owner_key),
                &program_id,
                filters,
                sort_results,
            )
            .await
            .map_err(|e| RpcCustomError::ScanError {
                message: e.to_string(),
            })
        } else {
            self.get_filtered_program_accounts(bank, program_id, filters, sort_results)
                .await
        }
    }
```

**File:** ledger-tool/src/args.rs (L67-90)
```rust
        Arg::with_name("accounts_index_limit")
            .long("accounts-index-limit")
            .value_name("VALUE")
            .takes_value(true)
            .possible_values(&[
                "minimal",
                "25GB",
                "50GB",
                "100GB",
                "200GB",
                "400GB",
                "800GB",
                "unlimited",
            ])
            .default_value("unlimited")
            .help("Sets the memory limit for the accounts index")
            .long_help(
                "Sets the memory limit for the accounts index. The size options will limit the \
                 accounts index memory to the specified value. E.g. \"50GB\" means the accounts \
                 index may use up to 50 GB of memory. The \"unlimited\" option keeps the entire \
                 accounts index in memory. All index entries that are not in memory are kept in \
                 the disk-backed index. The disk-backed index has lower performance; prefer \
                 higher explicit limits here.",
            ),
```

**File:** validator/src/commands/run/args.rs (L899-907)
```rust
    .arg(
        Arg::with_name("account_indexes")
            .long("account-index")
            .takes_value(true)
            .multiple(true)
            .possible_values(&["program-id", "spl-token-owner", "spl-token-mint"])
            .value_name("INDEX")
            .help("Enable an accounts index, indexed by the selected account field"),
    )
```
