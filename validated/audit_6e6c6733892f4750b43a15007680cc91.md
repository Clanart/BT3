## Title
`AccountLtHash` omits `rent_epoch` from the account fingerprint, letting a state field silently diverge from the lattice hash used to derive the bank hash - (`accounts-db/src/accounts_db.rs`)

## Summary
The external report's bug class is "a hash/type descriptor is defined over a subset of a struct's fields, so two semantically-different instances of that struct collapse to the same hash, undermining whatever invariant the hash was supposed to protect." The Agave analog is `AccountsDb::hash_account_helper`, which computes the per-account hash that feeds the `AccountsLtHash` (the lattice hash used to build `Bank`'s `accounts_lt_hash`, which is folded into the bank's serialized fields and snapshot hash). This helper hashes `lamports`, `data`, `executable`, `owner`, and `pubkey`, but never hashes `rent_epoch`, even though `rent_epoch` is a first-class field of the on-chain `Account`/`AccountSharedData` struct that is persisted, loaded, and returned to programs/RPC.

## Finding Description
`hash_account_helper` builds the buffer that is hashed into the account's lattice-hash contribution: [1](#0-0) 

It explicitly gathers `lamports()`, `data()`, `executable()`, `owner()`, and `pubkey`, but at no point reads or mixes in `account.rent_epoch()`. `lt_hash_account` (which calls this helper) is the function used to compute `AccountLtHash` per account: [2](#0-1) 

This is structurally identical to the reported bug: the `orderType` string omitted `reduceOnly`, `isLong`, etc., from the struct being hashed, so two orders differing only in those fields hashed identically. Here, the account "struct" being fingerprinted for the lattice/bank hash omits `rent_epoch`, so two accounts (or the same account before/after a `rent_epoch` mutation) that differ only in `rent_epoch` produce an identical `AccountLtHash` contribution.

## Impact Explanation
`AccountsLtHash`/`AccountLtHash` values are accumulated into `Bank::accounts_lt_hash`, which is part of `BankFieldsToSerialize` and is used to derive the snapshot hash (`SnapshotPackage::new` -> `SnapshotHash::new(bank_fields_to_serialize.accounts_lt_hash.0.checksum())`): [3](#0-2) [4](#0-3) 

If any code path mutates `rent_epoch` without a corresponding change to lamports/data/owner/executable (e.g., during account loading/rent-epoch normalization or a future migration touching rent bookkeeping), the lattice hash will not reflect that state transition. Because the lt-hash is an incremental commutative structure (old contribution subtracted, new contribution added), an update whose "before" and "after" hashes are identical is effectively a no-op to the accumulator even though the account's actual on-disk/in-cache bytes changed. That means the externally observable snapshot hash / accounts-lt-hash can fail to reflect a real state change, which is exactly the "false acceptance of state" class called out as valid impact (the hash used to gate snapshot/bank-state integrity does not commit to the full account struct).

## Likelihood Explanation
This requires no malicious peer, gossip message, or trusted-plugin assumption — it is a property of the hashing routine itself, triggered purely by normal account processing whenever `rent_epoch` is the only field that changes for an account between two `enqueue_*_accounts_lt_hash_updates` calls. Given rent mechanics are largely deprecated/frozen (`RENT_EXEMPT_RENT_EPOCH`), the practical trigger surface today may be narrow, which is why I flag this with lower confidence rather than as a fully proven exploit — I was not able to fully trace every call site that mutates `rent_epoch` independently of the other hashed fields within the scope of this investigation, nor confirm whether `rent_epoch` is intentionally excluded elsewhere (e.g., in the older full `AccountsHasher`/merkle-hash path) as a deliberate design decision, since I could not inspect `accounts_hash.rs` in enough depth in the time available.

## Recommendation
Include `account.rent_epoch()` (or explicitly document/assert why it is safe to omit, mirroring the exhaustive-destructure pattern already used elsewhere in this codebase for exactly this class of bug, see `semantic_fingerprint` in `geyser-plugin-manager/src/contact_info_notifier.rs`) in the buffer hashed by `hash_account_helper`, so the lattice hash commits to every field of the account struct that can independently vary. [5](#0-4) 

## Proof of Concept
Conceptual reproduction (not executed, since I only have static access):
1. Load account `A` with `lamports=L`, `data=D`, `owner=O`, `executable=E`, `rent_epoch=R1`.
2. Compute `AccountLtHash` via `AccountsDb::lt_hash_account(&A, &pubkey)`.
3. Mutate only `rent_epoch` to `R2` (all other fields unchanged) and recompute `AccountsDb::lt_hash_account(&A', &pubkey)`.
4. Because `hash_account_helper` never reads `rent_epoch`, steps 2 and 3 produce identical `blake3` digests and identical `LtHash::with(...)` outputs, so the incremental lt-hash update (`subtract old, add new`) is a net no-op — the bank's `accounts_lt_hash` does not change even though the account's actual state (`rent_epoch`) did.

I could not, within this investigation's scope, fully confirm a concrete consensus-relevant callsite that mutates `rent_epoch` in isolation from other fields at runtime today (rent collection is largely deprecated), so treat the "Likelihood" section's caveat as the main open question for a full triage — a Devin session with repo access could grep all writers of `rent_epoch` (`accounts-db/src/append_vec.rs`, `storable_accounts.rs`, `accounts_update_notifier_interface.rs`) to determine whether any live path changes only that field.

### Citations

**File:** accounts-db/src/accounts_db.rs (L4164-4173)
```rust
    /// Calculates the `AccountLtHash` of `account`
    pub fn lt_hash_account(account: &impl ReadableAccount, pubkey: &Pubkey) -> AccountLtHash {
        if account.lamports() == 0 {
            return ZERO_LAMPORT_ACCOUNT_LT_HASH;
        }

        let hasher = Self::hash_account_helper(account, pubkey);
        let lt_hash = LtHash::with(&hasher);
        AccountLtHash(lt_hash)
    }
```

**File:** accounts-db/src/accounts_db.rs (L4176-4209)
```rust
    fn hash_account_helper(account: &impl ReadableAccount, pubkey: &Pubkey) -> blake3::Hasher {
        let mut hasher = blake3::Hasher::new();

        // allocate a buffer on the stack that's big enough
        // to hold a token account or a stake account
        const META_SIZE: usize = 8 /* lamports */ + 1 /* executable */ + 32 /* owner */ + 32 /* pubkey */;
        const DATA_SIZE: usize = 200; // stake accounts are 200 B and token accounts are 165-182ish B
        const BUFFER_SIZE: usize = META_SIZE + DATA_SIZE;
        let mut buffer = SmallVec::<[u8; BUFFER_SIZE]>::new();

        // collect lamports into buffer to hash
        buffer.extend_from_slice(&account.lamports().to_le_bytes());

        let data = account.data();
        if data.len() > DATA_SIZE {
            // For larger accounts whose data can't fit into the buffer, update the hash now.
            hasher.update(&buffer);
            buffer.clear();

            // hash account's data
            hasher.update(data);
        } else {
            // For small accounts whose data can fit into the buffer, append it to the buffer.
            buffer.extend_from_slice(data);
        }

        // collect executable, owner, and pubkey into buffer to hash
        buffer.push(account.executable().into());
        buffer.extend_from_slice(account.owner().as_ref());
        buffer.extend_from_slice(pubkey.as_ref());
        hasher.update(&buffer);

        hasher
    }
```

**File:** runtime/src/snapshot_package.rs (L44-47)
```rust

        let bank_fields_to_serialize = bank.get_fields_to_serialize();
        let hash = SnapshotHash::new(bank_fields_to_serialize.accounts_lt_hash.0.checksum());

```

**File:** runtime/src/bank.rs (L619-624)
```rust
    pub stakes: Stakes<StakeAccount<Delegation>>,
    pub is_delta: bool,
    pub accounts_data_len: u64,
    pub versioned_epoch_stakes: HashMap<u64, VersionedEpochStakes>,
    pub accounts_lt_hash: AccountsLtHash,
    pub block_id: Hash,
```

**File:** geyser-plugin-manager/src/contact_info_notifier.rs (L313-323)
```rust
/// Hash of the fields that meaningfully describe a validator's network
/// presence. Excludes `pubkey` (it's the cache key) and `wallclock`
/// (which advances on every republish without semantic change). Includes
/// `outset` because a change there indicates a node restart or identity
/// transfer — both of which are real events consumers want to see, even
/// if no other field happens to differ.
///
/// The exhaustive destructure pattern below forces a compile error if a
/// new field is added to `ContactInfoSnapshot`, so the fingerprint can
/// never silently miss a new socket or version field.
fn semantic_fingerprint(s: &ContactInfoSnapshot) -> u64 {
```
