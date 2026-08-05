## Finding: Valid (Algorithmic Complexity / CPU Exhaustion)

### Title
Unbounded CPU cost in `RunLengthEncoding::to_slots` due to `take(MAX_SLOTS)` only bounding output count, not skipped run-length work - (File: `gossip/src/restart_crds_values.rs`)

### Summary
`RestartLastVotedForkSlots::MAX_BYTES` (824) and `RunLengthEncoding::new` only bound the size of *self-generated* run-length encodings; nothing on the deserialization/`Sanitize` path validates that an incoming `RunLengthEncoding(Vec<U16>)` obeys that bound. Each `U16` run count is decoded via `Leb128Int<u16>` and can independently be up to `65535`, so an attacker can pack many maximal-value runs into a wire buffer whose *encoded* size is small, then rely on `to_slots()`'s lazy iterator chain to spend CPU proportional to the sum of those run counts rather than to the number of encoded bytes. [1](#0-0) [2](#0-1) 

### Finding Description
`RunLengthEncoding::new` (encoder side) enforces `current_bytes <= MAX_BYTES` while building the vector, guaranteeing an *honestly constructed* `RunLengthEncoding` stays small: [3](#0-2) 

However, `RunLengthEncoding` derives plain `Deserialize`/`SchemaRead` with no custom validation, and `Sanitize for RestartLastVotedForkSlots` only checks `wallclock` and `last_voted_hash`, never `offsets`: [4](#0-3) 

So a peer can send an arbitrary `Vec<U16>` whose individual counts are each up to `u16::MAX` (65535), independent of how many bytes that costs on the wire (each `U16` leb128-encodes in as little as 1-3 bytes).

In `to_slots`, the iterator chain is:
```
.flat_map(|(bit_count, bit)| repeat_n(bit, bit_count))
.enumerate()
.filter(|(_, bit)| **bit == 1)
.map_while(...)
.take(MAX_SLOTS)
.take_while(...)
``` [5](#0-4) 

`.take(MAX_SLOTS)` only limits the count of items that survive the `filter` (i.e., bits equal to `1`). To produce a single filtered "1" item, `filter` must internally call `.next()` on the upstream `flat_map`/`repeat_n` for every skipped "0" in the current run - this work is *not* counted against `take()`'s limit. By alternating short "1" runs (count = 1, so each "one" run only contributes 1 to the output count) with long "0" runs (count = 65535 each), an attacker forces the iterator to enumerate on the order of `sum(counts)` elements while the number of items that actually reach `take()` stays far below `MAX_SLOTS`, so `take()` never terminates the walk early. The only real stopping condition becomes exhaustion of the `Vec<U16>` itself, i.e., the total work is `O(sum(counts))`, not `O(MAX_BYTES)` or `O(MAX_SLOTS)`.

### Impact Explanation
Within the CRDS-value wire budget the field is sized for (`MAX_BYTES = 824`, matching `MAX_CRDS_OBJECT_SIZE` minus header overhead), an attacker can fit roughly 270+ maximal 3-byte `U16(65535)` varints, yielding `sum(counts)` in the range of ~17-18 million enumerated elements per crafted `RestartLastVotedForkSlots` value versus the ~824 bytes actually transmitted - an amplification on the order of `MAX_SLOTS` (~65535x) over what the size bound is meant to guarantee. Each call to `to_slots()` on such a value costs CPU proportional to this inflated count rather than to the wire size, which is a genuine violation of the intended `O(MAX_BYTES)`-bounded design comment ("Per design doc... within 7 hours", `MAX_SLOTS = u16::MAX`). [6](#0-5) 

### Likelihood / Scope Caveat
This is a real defect in the code, but its practical exposure is narrower than "general gossip thread exhaustion": `to_slots()` on `RestartLastVotedForkSlots`/`RestartHeaviestFork` values is only invoked by the wen_restart coordination logic, which is only active when a validator operator has explicitly put the node into wen_restart mode (an opt-in, operator-triggered cluster-restart procedure), not during normal gossip/CRDS storage/propagation of arbitrary values. I was not able to locate the exact call site of `RestartLastVotedForkSlots::to_slots` within a wen_restart module in this index (searches only surfaced `to_slots` usages for the unrelated `EpochSlots` type in `gossip/src/epoch_slots.rs`, `core/src/cluster_info_vote_listener.rs`, and `core/src/cluster_slots_service/cluster_slots.rs`), so I could not fully verify how frequently/where in the codebase a malicious peer's crafted value would actually be fed into `to_slots()` outside of tests. This gap means the "repeated per-message CPU exhaustion in the gossip/wen_restart handling thread" impact claim is plausible for nodes actively running wen_restart, but not confirmed as a general always-on gossip-thread exposure for default validator operation.

### Recommendation
- Add validation in `Sanitize for RestartLastVotedForkSlots` (or in a dedicated deserialize-time check on `RunLengthEncoding`) that rejects any encoding where `num_encoded_slots()` (sum of counts) exceeds `RestartLastVotedForkSlots::MAX_SLOTS` or where the encoded byte cost exceeds `MAX_BYTES`, mirroring the encoder-side bound already implemented in `RunLengthEncoding::new`.
- Alternatively/additionally, rewrite `to_slots()` to skip zero-runs arithmetically (advance the running offset by `bit_count` directly for `bit == 0` instead of materializing/skip-iterating each element via `repeat_n`), which removes the amplification regardless of input validation.

### Proof of Concept
Construct `RunLengthEncoding(vec![U16(1), U16(65535), U16(1), U16(65535), ...])` (alternating short "one" runs with maximal "zero" runs) totaling ~270 entries to stay within the ~824-byte `MAX_BYTES` wire budget, wrap in `SlotsOffsets::RunLengthEncoding` and `RestartLastVotedForkSlots`, `wincode::deserialize` it (bypassing the encoder-side `MAX_BYTES` scan since deserialization uses plain derived `Deserialize`), and call `.to_slots(0)` with `last_voted_slot = u64::MAX`. The number of `Iterator::next()` calls performed by the underlying `flat_map`/`repeat_n` will be on the order of `sum(counts)` (~10-18 million) rather than bounded by `MAX_SLOTS` (65535) or the ~824-byte wire size, which can be confirmed by instrumenting/counting iterations against `RunLengthEncoding::to_slots` as defined at: [5](#0-4)

### Citations

**File:** gossip/src/restart_crds_values.rs (L73-85)
```rust
#[cfg_attr(feature = "frozen-abi", derive(AbiExample, StableAbi, StableAbiSample))]
#[derive(Deserialize, Serialize, Clone, Debug, PartialEq, Eq, SchemaWrite, SchemaRead)]
struct U16(
    #[serde(with = "serde_varint")]
    #[wincode(with = "Leb128Int<u16>")]
    u16,
);

// The vector always starts with 1. Encode number of 1's and 0's consecutively.
// For example, 110000111 is [2, 4, 3].
#[cfg_attr(feature = "frozen-abi", derive(AbiExample, StableAbi, StableAbiSample))]
#[derive(Deserialize, Serialize, Clone, Debug, PartialEq, Eq, SchemaWrite, SchemaRead)]
struct RunLengthEncoding(Vec<U16>);
```

**File:** gossip/src/restart_crds_values.rs (L98-103)
```rust
impl Sanitize for RestartLastVotedForkSlots {
    fn sanitize(&self) -> std::result::Result<(), SanitizeError> {
        sanitize_wallclock(self.wallclock)?;
        self.last_voted_hash.sanitize()
    }
}
```

**File:** gossip/src/restart_crds_values.rs (L105-110)
```rust
impl RestartLastVotedForkSlots {
    // This number is MAX_CRDS_OBJECT_SIZE - empty serialized RestartLastVotedForkSlots.
    const MAX_BYTES: usize = 824;

    // Per design doc, we should start wen_restart within 7 hours.
    pub const MAX_SLOTS: usize = u16::MAX as usize;
```

**File:** gossip/src/restart_crds_values.rs (L196-208)
```rust
impl RunLengthEncoding {
    fn new(bits: &BitVec<u8>) -> Self {
        let encoded = (0..bits.len())
            .map(|i| bits.get(i))
            .dedup_with_count()
            .map_while(|(count, _)| u16::try_from(count).ok())
            .scan(0, |current_bytes, count| {
                *current_bytes += (u16::BITS - count.leading_zeros()).div_ceil(7).max(1) as usize;
                (*current_bytes <= RestartLastVotedForkSlots::MAX_BYTES).then_some(U16(count))
            })
            .collect();
        Self(encoded)
    }
```

**File:** gossip/src/restart_crds_values.rs (L214-232)
```rust
    fn to_slots(&self, last_slot: Slot, min_slot: Slot) -> Vec<Slot> {
        let mut slots: Vec<Slot> = self
            .0
            .iter()
            .map(|bit_count| usize::from(bit_count.0))
            .zip([1, 0].iter().cycle())
            .flat_map(|(bit_count, bit)| std::iter::repeat_n(bit, bit_count))
            .enumerate()
            .filter(|(_, bit)| **bit == 1)
            .map_while(|(offset, _)| {
                let offset = Slot::try_from(offset).ok()?;
                last_slot.checked_sub(offset)
            })
            .take(RestartLastVotedForkSlots::MAX_SLOTS)
            .take_while(|slot| *slot >= min_slot)
            .collect();
        slots.reverse();
        slots
    }
```
