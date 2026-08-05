[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** gossip/src/crds.rs (L596-669)
```rust
    pub fn remove(&mut self, key: &CrdsValueLabel, now: u64) {
        let Some((index, _ /*label*/, value)) = self.table.swap_remove_full(key) else {
            return;
        };
        self.purged.push_back((*value.value.hash(), now));
        self.shards.remove(index, &value);
        match value.value.data() {
            CrdsData::ContactInfo(node) => {
                self.nodes.swap_remove(&index);
                // Tell any attached Geyser-side listener that this
                // validator is no longer in CRDS (timeout-based purge
                // via `purge_active`, or size-based trim via
                // `trim_crds_table`), so consumers can invalidate any
                // cached endpoint they hold instead of relying on a
                // wallclock-staleness heuristic.
                emit_contact_info_event(
                    self.contact_info_sender.as_ref(),
                    ContactInfoEvent::Removed(*node.pubkey()),
                );
            }
            CrdsData::Vote(_, _) => {
                self.votes.remove(&value.ordinal);
            }
            CrdsData::EpochSlots(_, _) => {
                self.epoch_slots.remove(&value.ordinal);
            }
            CrdsData::DuplicateShred(_, _) => {
                self.duplicate_shreds.remove(&value.ordinal);
            }
            _ => (),
        }
        self.entries.remove(&value.ordinal);
        // Remove the index from records associated with the value's pubkey.
        let pubkey = value.value.pubkey();
        let hash_map::Entry::Occupied(mut records_entry) = self.records.entry(pubkey) else {
            panic!("this should not happen!");
        };
        records_entry.get_mut().swap_remove(&index);
        if records_entry.get().is_empty() {
            records_entry.remove();
        }
        // If index == self.table.len(), then the removed entry was the last
        // entry in the table, in which case no other keys were modified.
        // Otherwise, the previously last element in the table is now moved to
        // the 'index' position; and so shards and nodes need to be updated
        // accordingly.
        let size = self.table.len();
        if index < size {
            let value = self.table.index(index);
            self.shards.remove(size, value);
            self.shards.insert(index, value);
            match value.value.data() {
                CrdsData::ContactInfo(_) => {
                    self.nodes.swap_remove(&size);
                    self.nodes.insert(index);
                }
                CrdsData::Vote(_, _) => {
                    self.votes.insert(value.ordinal, index);
                }
                CrdsData::EpochSlots(_, _) => {
                    self.epoch_slots.insert(value.ordinal, index);
                }
                CrdsData::DuplicateShred(_, _) => {
                    self.duplicate_shreds.insert(value.ordinal, index);
                }
                _ => (),
            };
            self.entries.insert(value.ordinal, index);
            let pubkey = value.value.pubkey();
            let records = self.records.get_mut(&pubkey).unwrap();
            records.swap_remove(&size);
            records.insert(index);
        }
    }
```

**File:** streamer/src/nonblocking/quic.rs (L964-1108)
```rust
    pub(crate) fn prune_oldest(&mut self, max_size: usize) -> usize {
        let mut num_pruned = 0;
        let key = |(_, connections): &(_, &Vec<_>)| {
            connections.iter().map(ConnectionEntry::last_update).min()
        };
        while self.total_size.saturating_sub(num_pruned) > max_size {
            match self.table.values().enumerate().min_by_key(key) {
                None => break,
                Some((index, connections)) => {
                    num_pruned += connections.len();
                    self.table.swap_remove_index(index);
                }
            }
        }
        self.total_size = self.total_size.saturating_sub(num_pruned);
        num_pruned
    }

    // Randomly selects sample_size many connections, evicts the one with the
    // lowest stake, and returns the number of pruned connections.
    // If the stakes of all the sampled connections are higher than the
    // threshold_stake, rejects the pruning attempt, and returns 0.
    pub(crate) fn prune_random(&mut self, sample_size: usize, threshold_stake: u64) -> usize {
        let num_pruned = std::iter::once(self.table.len())
            .filter(|&size| size > 0)
            .flat_map(|size| {
                let mut rng = rng();
                repeat_with(move || rng.random_range(0..size))
            })
            .map(|index| {
                let connection = self.table[index].first();
                let stake = connection.map(|connection: &ConnectionEntry<S>| connection.stake());
                (index, stake)
            })
            .take(sample_size)
            .min_by_key(|&(_, stake)| stake)
            .filter(|&(_, stake)| stake < Some(threshold_stake))
            .and_then(|(index, _)| self.table.swap_remove_index(index))
            .map(|(_, connections)| connections.len())
            .unwrap_or_default();
        self.total_size = self.total_size.saturating_sub(num_pruned);
        num_pruned
    }

    pub(crate) fn try_add_connection<F: FnOnce() -> Arc<S>>(
        &mut self,
        key: ConnectionTableKey,
        port: u16,
        client_connection_tracker: ClientConnectionTracker,
        connection: Option<Connection>,
        peer_type: ConnectionPeerType,
        last_update: Arc<AtomicU64>,
        max_connections_per_peer: usize,
        stream_counter_factory: F,
    ) -> Option<(Arc<AtomicU64>, CancellationToken, Arc<S>)> {
        let connection_entry = self.table.entry(key).or_default();
        let has_connection_capacity = connection_entry
            .len()
            .checked_add(1)
            .map(|c| c <= max_connections_per_peer)
            .unwrap_or(false);
        if has_connection_capacity {
            let cancel = self.cancel.child_token();
            let stream_counter = connection_entry
                .first()
                .map(|entry| entry.stream_counter.clone())
                .unwrap_or_else(stream_counter_factory);
            connection_entry.push(ConnectionEntry::new(
                cancel.clone(),
                peer_type,
                last_update.clone(),
                port,
                client_connection_tracker,
                connection,
                stream_counter.clone(),
            ));
            self.total_size += 1;
            Some((last_update, cancel, stream_counter))
        } else {
            if let Some(connection) = connection {
                connection.close(
                    CONNECTION_CLOSE_CODE_TOO_MANY.into(),
                    CONNECTION_CLOSE_REASON_TOO_MANY,
                );
            }
            None
        }
    }

    // Returns number of connections that were removed
    pub(crate) fn remove_connection(
        &mut self,
        key: ConnectionTableKey,
        port: u16,
        stable_id: usize,
    ) -> usize {
        if let Entry::Occupied(mut e) = self.table.entry(key) {
            let e_ref = e.get_mut();
            let old_size = e_ref.len();

            e_ref.retain(|connection_entry| {
                // Retain the connection entry if the port is different, or if the connection's
                // stable_id doesn't match the provided stable_id.
                // (Some unit tests do not fill in a valid connection in the table. To support that,
                // if the connection is none, the stable_id check is ignored. i.e. if the port matches,
                // the connection gets removed)
                connection_entry.port != port
                    || connection_entry
                        .connection
                        .as_ref()
                        .and_then(|connection| (connection.stable_id() != stable_id).then_some(0))
                        .is_some()
            });
            let new_size = e_ref.len();
            if e_ref.is_empty() {
                e.swap_remove_entry();
            }
            let connections_removed = old_size.saturating_sub(new_size);
            self.total_size = self.total_size.saturating_sub(connections_removed);
            connections_removed
        } else {
            0
        }
    }

    /// Removes all connections associated with `key`.
    ///
    /// Returns the number of removed connections.
    pub(crate) fn remove_connections_by_key(&mut self, key: ConnectionTableKey) -> usize {
        self.table
            .swap_remove(&key)
            .map(|connections| {
                let num_removed = connections.len();
                debug_assert!(
                    self.total_size >= num_removed,
                    "connection table size underflow while removing by key; total_size={}, \
                     removed={}",
                    self.total_size,
                    num_removed
                );
                self.total_size = self.total_size.saturating_sub(num_removed);
                num_removed
            })
            .unwrap_or_default()
    }
```

**File:** gossip/src/weighted_shuffle.rs (L166-170)
```rust
    fn remove_zero(&mut self, k: usize) {
        if let Some(index) = self.zeros.iter().position(|&ix| ix == k) {
            self.zeros.remove(index);
        }
    }
```

**File:** program-runtime/src/loaded_programs.rs (L882-930)
```rust
    pub fn evict_using_random_selection(&mut self, shrink_to_percent: Percent, now: Slot) {
        let mut candidates = self.get_flattened_entries();
        let mut rng = rng();
        self.stats
            .water_level
            .store(candidates.len() as u64, Ordering::Relaxed);
        let num_to_unload = candidates
            .len()
            .saturating_sub(percent_of_max_entries(shrink_to_percent));
        let mut sample_entry = |candidates: &Vec<(Pubkey, u64, Arc<ProgramCacheEntry>)>| {
            // gen_range is deprecated in favor of random_range in rand>=0.9, but we also get
            // rnd() from shuttle, which doesn't yet support rand 0.9 APIs
            #[cfg(feature = "shuttle-test")]
            let index = rng.gen_range(0..candidates.len());
            #[cfg(not(feature = "shuttle-test"))]
            let index = rng.random_range(0..candidates.len());
            let usage_counter = candidates
                .get(index)
                .expect("Failed to get cached entry")
                .2
                .retention_score();
            (index, usage_counter)
        };

        // Random sampling with just 2 choices can frequently lead to a situation where both
        // entries chosen have relatively high retention scores, having us to pick one out of two
        // poor options. We can tell what a relatively high retention score is, so we can make a
        // few additional samples until we hit some other entry that isn't as highly scoring.
        //
        // Note that the "high enough" compilation time and use count numbers used here are
        // relatively arbitrary.
        const MAX_ADDITIONAL_SAMPLES: usize = 3;
        let avoid_evicting_above_score = retention_score(now, 500 * EMA_SCALE, 500);
        for _ in 0..num_to_unload {
            let (mut index, mut score) = sample_entry(&candidates);
            for _ in 0..MAX_ADDITIONAL_SAMPLES {
                let (sample_index, sample_score) = sample_entry(&candidates);
                if score > sample_score {
                    index = sample_index;
                    score = sample_score;
                }
                if score < avoid_evicting_above_score {
                    break;
                }
            }
            let (id, last_modification_slot, entry) = candidates.swap_remove(index);
            self.unload_program_entry(id, last_modification_slot, &entry);
        }
    }
```

**File:** gossip/src/contact_info.rs (L418-443)
```rust
    // Removes the socket associated with the specified key.
    fn remove_socket(&mut self, key: u8) {
        if let Some(index) = self.sockets.iter().position(|entry| entry.key == key) {
            let entry = self.sockets.remove(index);
            if let Some(next_entry) = self.sockets.get_mut(index) {
                next_entry.offset += entry.offset;
            }
            self.maybe_remove_addr(entry.index);
            if let Some(entry) = self.cache.get_mut(usize::from(key)) {
                *entry = SOCKET_ADDR_UNSPECIFIED;
            }
        }
    }

    // Removes the IP address at the given index if
    // no socket entry references that index.
    fn maybe_remove_addr(&mut self, index: u8) {
        if !self.sockets.iter().any(|entry| entry.index == index) {
            self.addrs.remove(usize::from(index));
            for entry in &mut self.sockets {
                if entry.index > index {
                    entry.index -= 1;
                }
            }
        }
    }
```
