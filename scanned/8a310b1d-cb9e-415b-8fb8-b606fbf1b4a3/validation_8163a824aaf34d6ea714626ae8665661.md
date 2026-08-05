[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** accounts-db/src/accounts_index/secondary.rs (L177-209)
```rust
    fn remove_index_entries(&self, outer_key: &Pubkey, inner_key: &Pubkey) -> bool {
        let Some(inner_keys) = self.index.get_mut(outer_key) else {
            // we were told that inner_key is in the outer_key map,
            // so the outer_key map should exist!
            panic!(
                "{}: bad index: missing entry for outer_key={outer_key} (inner_key={inner_key})",
                self.metrics_name
            );
        };

        let was_removed = inner_keys.value().remove_inner_key(inner_key);
        if !was_removed {
            // we were told that inner_key is in the outer_key map,
            // so the outer_key map should contain the inner_key!
            panic!(
                "{}: bad index: missing entry for inner_key={inner_key} in map for \
                 outer_key={outer_key}",
                self.metrics_name
            );
        }

        // Before dropping the lock, check if the outer_key map is empty.
        // Because if it is *not* empty, we can skip checking again below.
        let is_outer_key_empty = inner_keys.is_empty();
        drop(inner_keys);

        if is_outer_key_empty {
            // If the outer_key map was empty, we'll check again and remove it if still empty.
            // If it is no longer empty, that is fine, it was re-added, and nothing to do here.
            self.index
                .remove_if(outer_key, |_, inner_keys| inner_keys.is_empty());
        }
        was_removed
```

**File:** accounts-db/src/accounts_index/secondary.rs (L220-245)
```rust
    pub fn remove_by_inner_key_if(&self, inner_key: &Pubkey, should_remove: impl Fn() -> bool) {
        // Note: Always lock the reverse-index first, so we synchronize with insert().
        let DashMapEntry::Occupied(reverse_index_entry) = self.reverse_index.entry(*inner_key)
        else {
            // if inner_key doesn't exist in the reverse-index, nothing to do here
            return;
        };

        // Re-check under the reverse-index entry lock. If the caller no longer wants the key
        // removed (e.g. it was concurrently re-added), leave its mapping in place.
        if !should_remove() {
            return;
        }

        // First go through the reverse-index and remove inner_key from all forward-indexes.
        let num_removed = reverse_index_entry
            .get()
            .write()
            .unwrap()
            .drain(..)
            .map(|outer_key| self.remove_index_entries(&outer_key, inner_key) as u64)
            .sum();

        // And now after removing inner_key from all forward-indexes,
        // remove its entry from the reverse-index.
        reverse_index_entry.remove();
```

**File:** accounts-db/src/accounts_index/secondary.rs (L289-327)
```rust
    #[test]
    #[should_panic(expected = "bad index: missing entry for outer_key=")]
    fn test_remove_by_inner_key_panics_on_stale_reverse_mapping() {
        let secondary_index =
            SecondaryIndex::<RwLockSecondaryIndexEntry>::new("test_secondary_index");
        let outer_key = Pubkey::new_unique();
        let inner_key = Pubkey::new_unique();

        // only add an entry to the reverse index, not the forward index
        secondary_index
            .reverse_index
            .insert(inner_key, RwLock::new(vec![outer_key]));

        secondary_index.remove_by_inner_key_if(&inner_key, || true);
    }

    // Ensures remove_by_inner() enforces invariant that inner_key must
    // have an entry in the outer_key's forward index map.
    #[test]
    #[should_panic(expected = "bad index: missing entry for inner_key=")]
    fn test_remove_by_inner_key_panics_on_stale_forward_mapping() {
        let secondary_index =
            SecondaryIndex::<RwLockSecondaryIndexEntry>::new("test_secondary_index");
        let inner_key = Pubkey::new_unique();
        let outer_key_1 = Pubkey::new_unique();
        let outer_key_2 = Pubkey::new_unique();

        secondary_index.insert(&outer_key_1, &inner_key);
        secondary_index.insert(&outer_key_2, &inner_key);

        // remove the inner key from the outer key's forward index map
        secondary_index
            .index
            .get(&outer_key_2)
            .unwrap()
            .remove_inner_key(&inner_key);

        secondary_index.remove_by_inner_key_if(&inner_key, || true);
    }
```
