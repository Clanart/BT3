## Title
Unauthenticated `from` field in `DuplicateShred` gossip payload lets an unprivileged peer collide/overwrite another validator's in-flight duplicate-shred reconstruction buffer, blocking legitimate duplicate-slot detection - (File: `gossip/src/duplicate_shred_handler.rs`)

## Summary
The reported Alchemix bug is a "fill someone else's capped, identity-keyed slot with unprivileged writes" primitive: an attacker who does not own a resource can still write into a data structure that is *keyed by another user's identity*, hitting a hard cap and denying that user legitimate future operations. The closest analog in this codebase is `DuplicateShredHandler`'s reassembly `buffer`, which is keyed by `(Slot, Pubkey)` where the `Pubkey` component (`from`) is taken verbatim from the un-authenticated payload field of `DuplicateShred`, not from the cryptographic origin of the gossiped CRDS value.

## Finding Description
`DuplicateShredHandler` buffers incoming chunks of a duplicate-shred proof in: [1](#0-0) 

Insertion is keyed purely by `(chunk.slot, chunk.from)`, and a fixed-size `[Option<DuplicateShred>; MAX_NUM_CHUNKS]` array is indexed by `chunk_index`: [2](#0-1) 

The `from` field is a plain, attacker-supplied `Pubkey` inside the `DuplicateShred` struct: [3](#0-2) 

Nowhere in `handle_shred_data`, `check_shreds`, or the CRDS ingestion path (`duplicate_shred_listener.rs`) is `chunk.from` cross-checked against the pubkey that actually signed/originated the CRDS value carrying it: [4](#0-3) 

The handler's own test helper confirms this: `create_duplicate_proof` takes a `sender_pubkey: Option<Pubkey>` completely independent of the `keypair` used to actually sign the constituent shreds, i.e. the "reporter" identity embedded in the payload is decoupled from the cryptographic signer: [5](#0-4) 

Because reconstruction is validated only by `check_shreds` against the *slot leader's* signature (to prove shred1/shred2 are genuinely conflicting), and never against `from`, any peer that can obtain (by observing gossip, or being leader for some slot) a pair of genuinely conflicting signed shreds can re-broadcast them with an arbitrary `from` pubkey — e.g. the pubkey of a legitimate, honest validator that is also reporting the same duplicate slot. Since the buffer key is `(slot, from)` and the array is indexed only by `chunk_index` with no per-message provenance, the attacker's spoofed chunks and the honest validator's real chunks land in the exact same `entry` slots and overwrite each other: [6](#0-5) 

This is the direct analog of the VotingEscrow bug: a hard-capped/identity-keyed structure (`MAX_DELEGATES` per delegate target vs. `MAX_NUM_CHUNKS` slots per `(slot, from)` buffer entry) that any unprivileged actor can write into using *another party's identity as the key*, corrupting or crowding out that party's legitimate state without needing to compromise their keys.

## Impact Explanation
By repeatedly injecting malformed/garbage chunk payloads under a spoofed `from` (a real honest validator's pubkey) for the same slot, an attacker can prevent that slot's chunk array from ever reaching a consistent, fully-assembled state (`entry.iter().flatten().count() == num_chunks`) with the honest validator's actual data, or force a reconstruction attempt that fails `into_shreds` validation. Since `self.consumed.insert(slot, true)` is only set on a *successful* store, repeated interference degrades/delays the cluster's ability to persist and signal a duplicate-slot proof via `duplicate_slots_sender`, which feeds the duplicate-block consensus-safety machinery. This is a griefing/denial-of-service on an unprivileged, network-reachable gossip path (not a "malicious validator" special-capability assumption — anyone connected via gossip can push `CrdsData::DuplicateShred`), degrading a consensus-safety detection mechanism rather than a hard crash.

## Likelihood Explanation
Moderate. The attacker does not need leader status or key compromise for the *general* griefing pattern (spoofing `from` while relaying/replaying already-observed genuine conflicting shred pairs, or being leader themselves for self-produced proofs), and the vulnerable code path (`handle_shred_data`) is reached for every incoming `DuplicateShred` chunk with no provenance check on `from`. The main constraint is that the attacker still needs access to a genuinely leader-signed conflicting shred pair for the targeted slot (`check_shreds` enforces this), which limits arbitrary slot targeting to slots where such a duplicate genuinely exists/has been observed on the wire.

## Recommendation
Bind `chunk.from` to the actual CRDS origin/signer pubkey of the gossiped value before using it as a buffer key (i.e., pass the CRDS record's verified pubkey into `handle_shred_data` instead of trusting the embedded payload field), or key the reassembly buffer by a value derived from the shred content itself (e.g. slot + shred index/merkle root) rather than by a self-reported, unauthenticated identity field.

## Proof of Concept
1. Node A (leader for slot S) or any node that observes a genuine `(shred1, shred2)` conflicting pair for slot S has access to `from_shred(shred1, my_pubkey, shred2.payload(), ...)` which only requires the two shreds to sigverify for the slot leader — see `check_shreds` — it never checks that `my_pubkey` (i.e. `from`) equals anything in particular.
2. An honest validator V independently constructs and gossips the real duplicate-shred proof for slot S with `from = V`.
3. Attacker A relays/crafts an equivalent chunk sequence but sets `from = V` as well (using the `sender_pubkey` override pattern shown in `create_duplicate_proof`, gossip/src/duplicate_shred_handler.rs:249-287) and injects a bogus/incomplete final chunk at an index V has not yet sent.
4. Both V's and A's chunks are written into the same `buffer[(S, V)]` array (gossip/src/duplicate_shred_handler.rs:121-127); A's spurious chunk can overwrite a slot index before V's real chunk arrives, causing `into_shreds` to fail or reconstruct incorrect data for slot S, preventing `store_duplicate_slot`/`duplicate_slots_sender` from firing on V's legitimate report.

### Citations

**File:** gossip/src/duplicate_shred_handler.rs (L18-23)
```rust
const MAX_NUM_CHUNKS: usize = 3;
// Limit number of entries per node.
const MAX_NUM_ENTRIES_PER_PUBKEY: usize = 128;
const BUFFER_CAPACITY: usize = 512 * MAX_NUM_ENTRIES_PER_PUBKEY;

type BufferEntry = [Option<DuplicateShred>; MAX_NUM_CHUNKS];
```

**File:** gossip/src/duplicate_shred_handler.rs (L108-127)
```rust
    fn handle_shred_data(&mut self, chunk: DuplicateShred) -> Result<(), Error> {
        if !self.should_consume_slot(chunk.slot) {
            return Ok(());
        }
        let slot = chunk.slot;
        let num_chunks = chunk.num_chunks();
        let chunk_index = chunk.chunk_index();
        if usize::from(num_chunks) > MAX_NUM_CHUNKS || chunk_index >= num_chunks {
            return Err(Error::InvalidChunkIndex {
                chunk_index,
                num_chunks,
            });
        }
        let entry = self.buffer.entry((chunk.slot, chunk.from)).or_default();
        *entry
            .get_mut(usize::from(chunk_index))
            .ok_or(Error::InvalidChunkIndex {
                chunk_index,
                num_chunks,
            })? = Some(chunk);
```

**File:** gossip/src/duplicate_shred_handler.rs (L249-287)
```rust
    fn create_duplicate_proof(
        keypair: Arc<Keypair>,
        sender_pubkey: Option<Pubkey>,
        slot: u64,
        expected_error: Option<Error>,
        chunk_size: usize,
        shred_version: u16,
    ) -> Result<impl Iterator<Item = DuplicateShred>, Error> {
        let my_keypair = match expected_error {
            Some(Error::InvalidSignature) => Arc::new(Keypair::new()),
            _ => keypair,
        };
        let mut rng = rand::rng();
        let shredder = Shredder::new(slot, slot - 1, 0, shred_version).unwrap();
        let next_shred_index = 353;
        let shred1 = new_rand_shred(&mut rng, next_shred_index, &shredder, &my_keypair);
        let shredder1 = Shredder::new(slot + 1, slot, 0, shred_version).unwrap();
        let shred2 = match expected_error {
            Some(Error::SlotMismatch) => {
                new_rand_shred(&mut rng, next_shred_index, &shredder1, &my_keypair)
            }
            Some(Error::InvalidDuplicateShreds) => shred1.clone(),
            _ => new_rand_shred(&mut rng, next_shred_index, &shredder, &my_keypair),
        };
        let sender = match sender_pubkey {
            Some(pubkey) => pubkey,
            None => my_keypair.pubkey(),
        };
        let chunks = from_shred(
            shred1,
            sender,
            shred2.payload().clone(),
            None::<fn(Slot) -> Option<Pubkey>>,
            timestamp(), // wallclock
            chunk_size,  // max_size
            shred_version,
        )?;
        Ok(chunks)
    }
```

**File:** gossip/src/duplicate_shred.rs (L37-51)
```rust
pub struct DuplicateShred {
    pub(crate) from: Pubkey,
    pub(crate) wallclock: u64,
    pub(crate) slot: Slot,
    _unused: u32,
    // NOTE: This field was previously typed as `ShredType`.
    // It is semantically unused, so we now deserialize it as a plain `u8`
    // to avoid strict enum validation errors on bad data.
    _unused_shred_type: u8,
    // Serialized DuplicateSlotProof split into chunks.
    num_chunks: u8,
    chunk_index: u8,
    #[serde(with = "serde_bytes")]
    chunk: Vec<u8>,
}
```

**File:** gossip/src/duplicate_shred_listener.rs (L49-64)
```rust
    // Here we are sending data one by one rather than in a batch because in the future
    // we may send different type of CrdsData to different senders.
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
