## Analysis

The claim is essentially correct, and traces to a real logic gap in `DuplicateShredHandler::handle_shred_data`.

### Verification of the mechanism

`self.consumed.insert(slot, true)` is only reached at the very end of the success path, after `duplicate_shred::into_shreds` returns `Ok`: [1](#0-0) 

Critically, the buffer entry for `(slot, pubkey)` is drained with `std::mem::take` *before* `slot_leader_at` and `into_shreds` are invoked: [2](#0-1) 

If `into_shreds` fails (e.g. bad signature, corrupt payload, `Error::InvalidSignature`, `Error::InvalidDuplicateSlotProof`, etc.), the `?` propagates the error out of `handle_shred_data` before line 155 (`self.consumed.insert(slot, true)`) executes, so `consumed` for that slot is never set to `true`. `handle()` just logs the error and returns: [3](#0-2) 

`should_consume_slot` caches a `false` verdict from `blockstore.has_duplicate_shreds_in_slot` in the `consumed` map the first time it's queried, and this cached `false` is cheap to re-check on every subsequent call, so the "already consumed" gate never blocks further attempts for that slot as long as no proof has actually been stored: [4](#0-3) [5](#0-4) 

With `num_chunks = 1`, `entry.iter().flatten().count() == usize::from(num_chunks)` is satisfied on the very first chunk, so a single garbage `DuplicateShred` value per attempt is sufficient to re-trigger `slot_leader_at` + `duplicate_shred::into_shreds` (deserialization + signature verification), each time failing and leaving the buffer/consumed state unchanged for the next retry: [6](#0-5) 

### Delivery path

These `DuplicateShred` values arrive via the gossip CRDS table through `DuplicateShredListener::recv_loop`, which polls `cluster_info.get_duplicate_shreds` in a loop and dispatches every received entry to the handler: [7](#0-6) 

Each `DuplicateShred` is embedded as a self-signed `CrdsData::DuplicateShred` value. An attacker only needs their own ad-hoc Ed25519 keypair to sign a fresh `CrdsValue` (no stake or membership check on the "from" field beyond matching their own signing key), and can trivially generate a new value each time (e.g. incrementing `wallclock`) so that `Crds::insert`'s `overrides()` check accepts it as a new, distinct entry rather than a duplicate push, causing it to be consumed by the cursor and delivered to the handler again: [8](#0-7) [9](#0-8) 

This confirms the reported path is real: producing new signed junk chunks is cheap for the attacker (one Ed25519 sign per attempt), while each delivered chunk forces the target to redo `slot_leader_at` plus `into_shreds`, which performs `wincode` deserialization of the packed `DuplicateSlotProof`, reconstructs two `Shred` objects, and (when the payload parses far enough) performs two Ed25519 `shred.verify()` calls against the real slot leader inside `check_shreds`: [10](#0-9) [11](#0-10) 

This is a genuine asymmetric-cost amplification (cheap sign vs. deserialize+2×verify), repeatable indefinitely for any slot that is not yet rooted (`slot > last_root && slot < last_root + cached_slots_in_epoch`), with no per-slot retry cap and no requirement that the attacker be a staked/known validator.

### Title
Gossip duplicate-shred handler never marks a slot `consumed` on verification failure, allowing unbounded repeated signature-verification work per slot - (File: `gossip/src/duplicate_shred_handler.rs`)

### Summary
`DuplicateShredHandler::handle_shred_data` only records `self.consumed.insert(slot, true)` after a duplicate-shred proof is *successfully* reconstructed and verified via `duplicate_shred::into_shreds`. Any proof that fails verification (bad signature, malformed payload, mismatched shreds, etc.) causes an early `?` return that skips this insert, while the per-`(slot, pubkey)` reassembly buffer has already been cleared via `std::mem::take` before the expensive verification step runs. Because `should_consume_slot` continues to permit processing for that slot indefinitely (it only blocks once a proof is actually stored in the blockstore), an attacker can repeatedly submit fresh, single-chunk (`num_chunks=1`), badly-signed `DuplicateShred` gossip values for the same slot to force the target validator to repeatedly execute `leader_schedule_cache.slot_leader_at` and `duplicate_shred::into_shreds` (deserialization plus up to two Ed25519 signature verifications).

### Finding Description
- `handle_shred_data` clears the reassembly buffer with `mem::take` before calling `slot_leader_at`/`into_shreds` [12](#0-11) .
- `self.consumed.insert(slot, true)` is reached only after `into_shreds` succeeds [13](#0-12) .
- Any error from `into_shreds` (e.g. `Error::InvalidSignature`, `Error::InvalidDuplicateSlotProof`, `Error::MissingDataChunk`) propagates via `?` without updating `consumed` [14](#0-13) .
- `should_consume_slot`/`should_consume_slot` free function caches only a `false` ("not yet consumed") verdict and keeps allowing reprocessing [5](#0-4) .
- Delivery is driven purely by the gossip listener loop pulling from the CRDS table with no per-slot/per-sender retry throttling in the handler itself [7](#0-6) .

### Impact Explanation
This allows a remote, unprivileged (unstaked) network participant who can reach the target's gossip port to force repeated CPU-bound work (proof deserialization plus signature verification) on the single gossip-consumer thread (`solCiEntryLstnr`) for any not-yet-rooted slot, with no cap on the number of retries as long as the slot remains within the active window (`last_root < slot < last_root + cached_slots_in_epoch`). This falls into the "non-RPC remote exhaustion/crash"/degradation category. The per-message amplification is modest (roughly the cost of one Ed25519 sign by the attacker vs. deserialization + up to two Ed25519 verifications by the target), so the severity is a degradation/DoS of a single background thread rather than a consensus-affecting or fund-loss bug.

### Likelihood Explanation
Moderate. It requires only an arbitrary keypair (Sybil identity, no stake) and the ability to reach the target's gossip endpoint with distinct, freshly-signed CRDS values (e.g. incrementing wallclock) to avoid being treated as duplicate pushes and thus be re-delivered by the cursor-based listener.

### Recommendation
Mark the slot as consumed (or track failed-attempt counts / apply per-slot or per-sender rate limiting) even when `into_shreds` fails with a non-critical (attacker-controlled) error, so that a single bad proof cannot be resubmitted indefinitely for the same slot; alternatively, cache negative verification results per `(slot)` or `(slot, pubkey)` with a short-lived backoff before allowing reprocessing.

### Proof of Concept
1. Construct a `DuplicateShred` gossip value with `num_chunks = 1`, `chunk_index = 0`, and a `chunk` payload that deserializes to a `DuplicateSlotProof` with random/invalid shred signatures (e.g. reuse `create_duplicate_proof(..., Some(Error::InvalidSignature), ...)` from the test helper as a template) — this reliably triggers the `into_shreds` failure path shown in the existing `test_handle_mixed_entries` test [15](#0-14) .
2. Sign this value with a fresh keypair and push it via gossip repeatedly for the same slot, incrementing `wallclock` each time so `Crds::insert` treats each as a new entry rather than a duplicate push [16](#0-15) .
3. Observe that `blockstore.has_duplicate_shreds_in_slot(slot)` never becomes true, and measure CPU/time growth in the `solCiEntryLstnr` thread as the number of submitted garbage proofs increases, confirming `slot_leader_at`/`into_shreds` is re-invoked on every submission.

### Citations

**File:** gossip/src/duplicate_shred_handler.rs (L60-72)
```rust
        if let Err(error) = self.handle_shred_data(shred_data) {
            if error.is_non_critical() {
                info!(
                    "Received invalid duplicate shred proof from {pubkey} for slot {slot}: \
                     {error:?}"
                );
            } else {
                error!(
                    "Unable to process duplicate shred proof from {pubkey} for slot {slot}: \
                     {error:?}"
                );
            }
        }
```

**File:** gossip/src/duplicate_shred_handler.rs (L121-156)
```rust
        let entry = self.buffer.entry((chunk.slot, chunk.from)).or_default();
        *entry
            .get_mut(usize::from(chunk_index))
            .ok_or(Error::InvalidChunkIndex {
                chunk_index,
                num_chunks,
            })? = Some(chunk);
        // If all chunks are already received, reconstruct and store
        // the duplicate slot proof in blockstore
        if entry.iter().flatten().count() == usize::from(num_chunks) {
            let chunks = std::mem::take(entry).into_iter().flatten();
            let slot_leader = self
                .leader_schedule_cache
                .slot_leader_at(slot, /*bank:*/ None)
                .ok_or(Error::UnknownSlotLeader(slot))?;
            let (shred1, shred2) =
                duplicate_shred::into_shreds(&slot_leader.id, chunks, self.shred_version)?;
            if !self.blockstore.has_duplicate_shreds_in_slot(slot) {
                self.blockstore.store_duplicate_slot(
                    slot,
                    shred1.into_payload(),
                    shred2.into_payload(),
                )?;

                // Notify duplicate consensus state machine. Drop if channel is over 50% full
                // to avoid blocking replay.
                if self.duplicate_slots_sender.len() * 2
                    < self.duplicate_slots_sender.capacity().unwrap_or(usize::MAX)
                {
                    self.duplicate_slots_sender
                        .try_send(slot)
                        .map_err(|_| Error::DuplicateSlotSenderFailure)?;
                }
            }
            self.consumed.insert(slot, true);
        }
```

**File:** gossip/src/duplicate_shred_handler.rs (L160-164)
```rust
    fn should_consume_slot(&mut self, slot: Slot) -> bool {
        slot > self.last_root
            && slot < self.last_root.saturating_add(self.cached_slots_in_epoch)
            && should_consume_slot(slot, &self.blockstore, &mut self.consumed)
    }
```

**File:** gossip/src/duplicate_shred_handler.rs (L213-221)
```rust
fn should_consume_slot(
    slot: Slot,
    blockstore: &Blockstore,
    consumed: &mut HashMap<Slot, bool>,
) -> bool {
    !*consumed
        .entry(slot)
        .or_insert_with(|| blockstore.has_duplicate_shreds_in_slot(slot))
}
```

**File:** gossip/src/duplicate_shred_handler.rs (L368-392)
```rust
        // Test all kinds of bad proofs.
        for error in [
            Error::InvalidSignature,
            Error::SlotMismatch,
            Error::InvalidDuplicateShreds,
        ] {
            let proof_result = create_duplicate_proof(
                my_keypair.clone(),
                None,
                start_slot + 2,
                Some(error),
                DUPLICATE_SHRED_MAX_PAYLOAD_SIZE,
                shred_version,
            );
            match proof_result {
                Err(_) => (),
                Ok(chunks) => {
                    for chunk in chunks {
                        duplicate_shred_handler.handle(chunk);
                    }
                    assert!(!blockstore.has_duplicate_shreds_in_slot(start_slot + 2));
                    assert!(receiver.is_empty());
                }
            }
        }
```

**File:** gossip/src/duplicate_shred_listener.rs (L51-64)
```rust
    fn recv_loop(
        exit: Arc<AtomicBool>,
        cluster_info: &ClusterInfo,
        mut handler: impl DuplicateShredHandlerTrait + 'static,
    ) {
        let mut cursor = Cursor::default();
        while !exit.load(Ordering::Relaxed) {
            let entries: Vec<DuplicateShred> = cluster_info.get_duplicate_shreds(&mut cursor);
            for x in entries {
                handler.handle(x);
            }
            sleep(Duration::from_millis(GOSSIP_SLEEP_MILLIS));
        }
    }
```

**File:** gossip/src/crds.rs (L193-214)
```rust
// Returns true if the first value updates the 2nd one.
// Both values should have the same key/label.
fn overrides(value: &CrdsValue, other: &VersionedCrdsValue) -> bool {
    assert_eq!(value.label(), other.value.label(), "labels mismatch!");
    // Contact-infos are special cased so that if there are
    // two running instances of the same node, the more recent start is
    // propagated through gossip regardless of wallclocks.
    if let CrdsData::ContactInfo(value) = value.data()
        && let CrdsData::ContactInfo(other) = other.value.data()
        && let Some(out) = value.overrides(other)
    {
        return out;
    }
    match value.wallclock().cmp(&other.value.wallclock()) {
        Ordering::Less => false,
        Ordering::Greater => true,
        // Ties should be broken in a deterministic way across the cluster.
        // For backward compatibility this is done by comparing hash of
        // serialized values.
        Ordering::Equal => other.value.hash() < value.hash(),
    }
}
```

**File:** gossip/src/crds.rs (L261-299)
```rust
    pub fn insert(
        &mut self,
        value: CrdsValue,
        now: u64,
        route: GossipRoute,
    ) -> Result<(), CrdsError> {
        let label = value.label();
        let pubkey = value.pubkey();
        let value = VersionedCrdsValue::new(value, self.cursor, now, route);
        let mut stats = self.stats.lock().unwrap();
        match self.table.entry(label) {
            Entry::Vacant(entry) => {
                stats.record_insert(&value, route);
                let entry_index = entry.index();
                self.shards.insert(entry_index, &value);
                match value.value.data() {
                    CrdsData::ContactInfo(node) => {
                        self.nodes.insert(entry_index);
                        emit_contact_info_event(
                            self.contact_info_sender.as_ref(),
                            ContactInfoEvent::Updated(ContactInfoSnapshot::from(node)),
                        );
                    }
                    CrdsData::Vote(_, _) => {
                        self.votes.insert(value.ordinal, entry_index);
                    }
                    CrdsData::EpochSlots(_, _) => {
                        self.epoch_slots.insert(value.ordinal, entry_index);
                    }
                    CrdsData::DuplicateShred(_, _) => {
                        self.duplicate_shreds.insert(value.ordinal, entry_index);
                    }
                    _ => (),
                };
                self.entries.insert(value.ordinal, entry_index);
                self.records.entry(pubkey).or_default().insert(entry_index);
                self.cursor.consume(value.ordinal);
                entry.insert(value);
                Ok(())
```

**File:** gossip/src/crds.rs (L301-339)
```rust
            Entry::Occupied(mut entry) if overrides(&value.value, entry.get()) => {
                stats.record_insert(&value, route);
                let entry_index = entry.index();
                self.shards.remove(entry_index, entry.get());
                self.shards.insert(entry_index, &value);
                match value.value.data() {
                    CrdsData::ContactInfo(node) => {
                        // self.nodes does not need to be updated since the
                        // entry at this index was and stays contact-info.
                        debug_assert_matches!(entry.get().value.data(), CrdsData::ContactInfo(_));
                        emit_contact_info_event(
                            self.contact_info_sender.as_ref(),
                            ContactInfoEvent::Updated(ContactInfoSnapshot::from(node)),
                        );
                    }
                    CrdsData::Vote(_, _) => {
                        self.votes.remove(&entry.get().ordinal);
                        self.votes.insert(value.ordinal, entry_index);
                    }
                    CrdsData::EpochSlots(_, _) => {
                        self.epoch_slots.remove(&entry.get().ordinal);
                        self.epoch_slots.insert(value.ordinal, entry_index);
                    }
                    CrdsData::DuplicateShred(_, _) => {
                        self.duplicate_shreds.remove(&entry.get().ordinal);
                        self.duplicate_shreds.insert(value.ordinal, entry_index);
                    }
                    _ => (),
                }
                self.entries.remove(&entry.get().ordinal);
                self.entries.insert(value.ordinal, entry_index);
                // As long as the pubkey does not change, self.records
                // does not need to be updated.
                debug_assert_eq!(entry.get().value.pubkey(), pubkey);
                self.cursor.consume(value.ordinal);
                self.purged.push_back((*entry.get().value.hash(), now));
                entry.insert(value);
                Ok(())
            }
```

**File:** gossip/src/duplicate_shred.rs (L90-160)
```rust
#[derive(Debug, Error)]
pub enum Error {
    #[error("block store save error")]
    BlockstoreInsertFailed(#[from] BlockstoreError),
    #[error("data chunk mismatch")]
    DataChunkMismatch,
    #[error("unable to send duplicate slot to state machine")]
    DuplicateSlotSenderFailure,
    #[error("invalid chunk_index: {chunk_index}, num_chunks: {num_chunks}")]
    InvalidChunkIndex { chunk_index: u8, num_chunks: u8 },
    #[error("invalid duplicate shreds")]
    InvalidDuplicateShreds,
    #[error("invalid duplicate slot proof")]
    InvalidDuplicateSlotProof,
    #[error("invalid erasure meta conflict")]
    InvalidErasureMetaConflict,
    #[error("invalid last index conflict")]
    InvalidLastIndexConflict,
    #[error("invalid shred version: {0}")]
    InvalidShredVersion(u16),
    #[error("invalid signature")]
    InvalidSignature,
    #[error("invalid size limit")]
    InvalidSizeLimit,
    #[error(transparent)]
    InvalidShred(#[from] shred::Error),
    #[error("number of chunks mismatch")]
    NumChunksMismatch,
    #[error("missing data chunk")]
    MissingDataChunk,
    #[error("wincode deserialization error")]
    WincodeReadError(#[from] ReadError),
    #[error("wincode serialization error")]
    WincodeWriteError(#[from] WriteError),
    #[error("shred type mismatch")]
    ShredTypeMismatch,
    #[error("slot mismatch")]
    SlotMismatch,
    #[error("type conversion error")]
    TryFromIntError(#[from] TryFromIntError),
    #[error("unknown slot leader: {0}")]
    UnknownSlotLeader(Slot),
}

impl Error {
    /// Errors indicating that the initial node submitted an invalid duplicate proof case
    pub(crate) fn is_non_critical(&self) -> bool {
        match self {
            Self::SlotMismatch
            | Self::InvalidShredVersion(_)
            | Self::InvalidSignature
            | Self::ShredTypeMismatch
            | Self::InvalidDuplicateShreds
            | Self::InvalidLastIndexConflict
            | Self::InvalidErasureMetaConflict => true,
            Self::BlockstoreInsertFailed(_)
            | Self::DataChunkMismatch
            | Self::DuplicateSlotSenderFailure
            | Self::InvalidChunkIndex { .. }
            | Self::InvalidDuplicateSlotProof
            | Self::InvalidSizeLimit
            | Self::InvalidShred(_)
            | Self::NumChunksMismatch
            | Self::MissingDataChunk
            | Self::WincodeReadError(_)
            | Self::WincodeWriteError(_)
            | Self::TryFromIntError(_)
            | Self::UnknownSlotLeader(_) => false,
        }
    }
}
```

**File:** gossip/src/duplicate_shred.rs (L174-200)
```rust
fn check_shreds<F>(
    leader_schedule: Option<F>,
    shred1: &Shred,
    shred2: &Shred,
    shred_version: u16,
) -> Result<(), Error>
where
    F: FnOnce(Slot) -> Option<Pubkey>,
{
    if shred1.slot() != shred2.slot() {
        return Err(Error::SlotMismatch);
    }

    if shred1.version() != shred_version {
        return Err(Error::InvalidShredVersion(shred1.version()));
    }
    if shred2.version() != shred_version {
        return Err(Error::InvalidShredVersion(shred2.version()));
    }

    if let Some(leader_schedule) = leader_schedule {
        let slot_leader =
            leader_schedule(shred1.slot()).ok_or(Error::UnknownSlotLeader(shred1.slot()))?;
        if !shred1.verify(&slot_leader) || !shred2.verify(&slot_leader) {
            return Err(Error::InvalidSignature);
        }
    }
```

**File:** gossip/src/duplicate_shred.rs (L313-363)
```rust
pub(crate) fn into_shreds(
    slot_leader: &Pubkey,
    chunks: impl IntoIterator<Item = DuplicateShred>,
    shred_version: u16,
) -> Result<(Shred, Shred), Error> {
    let mut chunks = chunks.into_iter();
    let DuplicateShred {
        slot,
        num_chunks,
        chunk_index,
        chunk,
        ..
    } = chunks.next().ok_or(Error::InvalidDuplicateShreds)?;
    let check_chunk = check_chunk(slot, num_chunks);
    let mut data = HashMap::new();
    data.insert(chunk_index, chunk);
    for chunk in chunks {
        check_chunk(&chunk)?;
        match data.entry(chunk.chunk_index) {
            Entry::Vacant(entry) => {
                entry.insert(chunk.chunk);
            }
            Entry::Occupied(entry) => {
                if *entry.get() != chunk.chunk {
                    return Err(Error::DataChunkMismatch);
                }
            }
        }
    }
    if data.len() != num_chunks as usize {
        return Err(Error::MissingDataChunk);
    }
    let data = (0..num_chunks).map(|k| data.remove(&k).unwrap()).concat();
    let proof: DuplicateSlotProof = wincode::deserialize(&data)?;
    if proof.shred1 == proof.shred2 {
        return Err(Error::InvalidDuplicateSlotProof);
    }
    let shred1 = Shred::new_from_serialized_shred(proof.shred1)?;
    let shred2 = Shred::new_from_serialized_shred(proof.shred2)?;

    if shred1.slot() != slot || shred2.slot() != slot {
        Err(Error::SlotMismatch)
    } else {
        check_shreds(
            Some(|_| Some(slot_leader).copied()),
            &shred1,
            &shred2,
            shred_version,
        )?;
        Ok((shred1, shred2))
    }
```
