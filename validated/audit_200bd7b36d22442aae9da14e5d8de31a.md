No vulnerability found for this question.

**Why the premise doesn't hold:**

`ScanGuard::max_root` is captured **once** at construction and stored as an immutable field, not recomputed on each call. `ScanGuard::try_new` fetches the current root via `max_root_inclusive_fn()` while holding the `ongoing_scan_roots` write lock, and stores it as `self.max_root` [1](#0-0) . `should_use_ancestors` and `max_root()` both simply read `self.max_root`, which cannot change after the guard is created [2](#0-1) [3](#0-2) . So there is no "stale snapshot" problem within a single `ScanGuard` instance — there is only ever one snapshot of `max_root`, fixed for the guard's lifetime.

Additionally, `try_new` pins that captured `max_root` into `ongoing_scan_roots` for the duration of the scan, which is checked by `clean_accounts` so cleanup cannot advance past this pinned root while the scan is in flight [4](#0-3) . This means even if `BankForks::set_root` advances the root concurrently (fork switch), the state as of the guard's fixed `max_root` is guaranteed to still be persisted and not cleaned up.

The doc comments on `should_use_ancestors` explicitly analyze this exact race (root advancing during a scan across diverging forks) and provide a correctness proof: for any bank `B` descended from the guard's `max_root`, `B.ancestors` always contains `max_root` regardless of any later `set_root` calls, and when it doesn't (case 2 fork/ancestor scenario), the fallback to root-only scanning bounded by the pinned `max_root` is exactly the safe, deterministic view of the pre-cleanup state [5](#0-4) . This is corroborated by the concurrency test that explicitly roots a new slot mid-scan and asserts the scan does not surface the newer, unpinned data [6](#0-5) .

So the described attack — a fork-switch changing `max_root` "between `try_new` and a subsequent `should_use_ancestors` check on the same `ScanGuard`" — cannot occur because that value is immutable on the guard after construction, and the pinning mechanism protects the state at that captured root, not a race-prone re-read.

### Citations

**File:** accounts-db/src/accounts_scan.rs (L119-151)
```rust
        let max_root_inclusive = {
            let mut w_ongoing_scan_roots = scan_tracker
                // This lock is also grabbed by clean_accounts(), so clean
                // has at most cleaned up to the current `max_root` (since
                // clean only happens *after* BankForks::set_root() which sets
                // the `max_root`)
                .ongoing_scan_roots
                .write()
                .unwrap();
            // `max_root()` grabs a lock while
            // the `ongoing_scan_roots` lock is held,
            // make sure inverse doesn't happen to avoid
            // deadlock
            let max_root_inclusive = max_root_inclusive_fn();
            if let Some(min_ongoing_scan_root) =
                ScanTracker::min_ongoing_scan_root_from_btree(&w_ongoing_scan_roots)
                && min_ongoing_scan_root < max_root_inclusive
            {
                let current = max_root_inclusive - min_ongoing_scan_root;
                scan_tracker
                    .max_distance_to_min_scan_slot
                    .fetch_max(current, Ordering::Relaxed);
            }
            *w_ongoing_scan_roots.entry(max_root_inclusive).or_default() += 1;
            max_root_inclusive
        };

        scan_tracker.active_scans.fetch_add(1, Ordering::Relaxed);
        Some(Self {
            scan_tracker,
            max_root: max_root_inclusive,
            scan_bank_id,
        })
```

**File:** accounts-db/src/accounts_scan.rs (L154-157)
```rust
    /// The inclusive max root pinned by this scan guard.
    pub(crate) fn max_root(&self) -> Slot {
        self.max_root
    }
```

**File:** accounts-db/src/accounts_scan.rs (L159-231)
```rust
    /// Returns true if ancestors should be used, or false if the scan's bank
    /// is not descended from `max_root` (different fork or ancestor of
    /// `max_root`).
    ///
    /// For any bank `B` descended from the current `max_root`, it must be true
    /// that `B.ancestors.contains(max_root)`, regardless of squash behavior.
    /// (Proof: at startup max_root is the greatest root from the snapshot, and
    /// on each `set_root(R_new)` where `R_new > R`, every surviving descendant
    /// of `R_new` was also a descendant of `R` and therefore has `R_new` in its
    /// ancestors.)
    ///
    /// If `max_root` is **not** in `ancestors`, the bank is either:
    /// 1. on a different fork, or
    /// 2. an ancestor of `max_root`.
    ///
    /// In both cases the provided ancestors may reference slots that have
    /// already been cleaned, so we fall back to an empty ancestor set and rely
    /// only on roots (bounded by `max_root`).
    ///
    /// ```text
    ///             slot 0
    ///               |
    ///             slot 1
    ///           /        \
    ///      slot 2         |
    ///         |       slot 3 (max root)
    ///     slot 4 (scan)
    /// ```
    ///
    /// By the time the scan on slot 4 is called, slot 2 may already have been
    /// cleaned by a clean on slot 3, but slot 4 may not have been cleaned.
    /// The state in slot 2 would have been purged and is not saved in any roots.
    /// In this case, a scan on slot 4 wouldn't accurately reflect the state
    /// when bank 4 was frozen, so we default to a scan on the latest roots by
    /// removing all `ancestors`.
    ///
    /// After calling this, there are two cases:
    ///
    /// 1) **Ancestors is empty** (this method returned `false`): the scan behaves
    ///    like a scan on a rooted bank. `ongoing_scan_roots` protects the roots
    ///    needed by the scan, and passing `max_root` to the scan ensures newer
    ///    roots don't appear in the results.
    ///
    /// 2) **Ancestors is non-empty** (this method returned `true`): the fork
    ///    structure must look something like:
    ///
    /// ```text
    ///            slot 0
    ///              |
    ///        slot 1 (max_root)
    ///        /            \
    ///   slot 2              |
    ///      |            slot 3 (potential newer max root)
    ///    slot 4
    ///      |
    ///   slot 5 (scan)
    /// ```
    ///
    ///    Consider both types of ancestors, `ancestor <= max_root` and
    ///    `ancestor > max_root`, where `max_root == 1` as illustrated above.
    ///
    ///    a) The set of `ancestors <= max_root` are all rooted, which means their
    ///       state is protected by the same guarantees as case 1.
    ///
    ///    b) The `ancestors > max_root` have at least one reference discoverable
    ///       through the chain of `Bank::BankRc::parent` starting from the calling
    ///       bank. For instance bank 5's parent reference keeps bank 4 alive, which
    ///       prevents `Bank::drop()` from running and cleaning up bank 4.
    ///       Furthermore, no cleans can happen past the saved `max_root == 1`, so a
    ///       potential newer max root at slot 3 will not clean up any of the
    ///       ancestors > 1, and slot 4 will not be cleaned in the middle of the
    ///       scan either. (NOTE: similar reasoning is employed for the `assert!()`
    ///       justification in `AccountsDb::retry_to_get_account_accessor`.)
```

**File:** accounts-db/src/accounts_scan.rs (L232-234)
```rust
    pub(crate) fn should_use_ancestors(&self, ancestors: &Ancestors) -> bool {
        ancestors.contains_key(&self.max_root)
    }
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L7300-7355)
```rust
    db.store_for_tests((3, &[(&pubkey_new, &make_token_account(99))][..]));

    // Root slot 2 last — the scan guard will capture max_root = 2 because slot 3
    // is still unrooted when index_scan_accounts is called below.
    db.add_root_and_flush_write_cache(2);

    // The root thread waits for a signal from inside the scan callback, then
    // roots slot 3 mid-scan. The scan must not surface pubkey_new despite slot 3
    // becoming a root before the scan finishes.
    let start_rooting = Arc::new(AtomicBool::new(false));
    let done_rooting = Arc::new(AtomicBool::new(false));

    let root_thread = {
        let rooting_db = db.clone();
        let start_rooting = start_rooting.clone();
        let done_rooting = done_rooting.clone();
        Builder::new()
            .name("root-slot-3".into())
            .spawn(move || {
                while !start_rooting.load(Ordering::Acquire) {
                    thread::yield_now();
                }
                rooting_db.add_root_and_flush_write_cache(3);
                done_rooting.store(true, Ordering::Release);
            })
            .unwrap()
    };

    let ancestors = Ancestors::from(vec![0, 1]);
    let mut found_pubkeys = vec![];
    let mut signalled = false;

    db.index_scan_accounts(
        &ancestors,
        0,
        IndexKey::SplTokenMint(mint_key),
        |maybe_account| {
            if let Some((pubkey, _, _)) = maybe_account {
                if !signalled {
                    signalled = true;
                    start_rooting.store(true, Ordering::Release);
                    while !done_rooting.load(Ordering::Acquire) {
                        thread::yield_now();
                    }
                }
                found_pubkeys.push(*pubkey);
            }
        },
        &ScanConfig::default(),
    )
    .unwrap();

    root_thread.join().unwrap();

    // slot 3 was rooted after the scan guard's max_root (= 2) was established.
    assert!(!found_pubkeys.contains(&pubkey_new));
```
