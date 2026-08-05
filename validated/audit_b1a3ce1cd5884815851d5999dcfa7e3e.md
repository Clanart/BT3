Based on my review of `gossip/src/restart_crds_values.rs`, the amplification mechanism described in the question is real and present in the code as written, though I was unable to fully confirm from the index how often `to_slots` is invoked in the hot gossip-ingest path (only one call site was found in `gossip/src/cluster_info.rs`, whose full body I could not load before running out of iterations — this is a limitation worth flagging to a Devin session for deeper tracing).

### Title
RunLengthEncoding wire-to-compute amplification in `RestartLastVotedForkSlots::to_slots` - (File: `gossip/src/restart_crds_values.rs`)

### Summary
`RunLengthEncoding::to_slots` [1](#0-0)  expands every `U16` run-length entry into a virtual bit stream via `flat_map(... repeat_n(bit, bit_count))` before filtering for set bits, and only applies `.take(RestartLastVotedForkSlots::MAX_SLOTS)` *after* the `.filter(|(_, bit)| **bit == 1)` stage. Because `take()` only counts items that already passed the filter, an attacker can craft a `Vec<U16>` where nearly all runs are zero-runs (bit=0, which never satisfy the filter and are therefore never counted against the `MAX_SLOTS` cap), forcing the iterator to fully traverse every element of every run regardless of the cap.

### Finding Description
`RestartLastVotedForkSlots::new()` enforces the `MAX_BYTES`/`MAX_SLOTS` invariants only when a value is *constructed locally* via `RunLengthEncoding::new` [2](#0-1) . Nothing in `Sanitize for RestartLastVotedForkSlots` [3](#0-2)  validates the `offsets` field's contents (it only checks `wallclock` and `last_voted_hash`), and `num_encoded_slots()` [4](#0-3)  is never called on the receive/deserialize path. A remote peer can therefore submit an arbitrary `SlotsOffsets::RunLengthEncoding(Vec<U16>)` as long as the overall CRDS value stays under `MAX_CRDS_OBJECT_SIZE`.

Each `U16` count uses a Leb128 varint (`Leb128Int<u16>`) [5](#0-4) , so a value of `65535` costs 3 bytes on the wire, matching the byte-cost formula the constructor itself uses: `(u16::BITS - count.leading_zeros()).div_ceil(7).max(1)` [6](#0-5) .

Since `to_slots` alternates bit=1/bit=0 strictly by index parity via `.zip([1, 0].iter().cycle())` [7](#0-6) , a crafted payload does not need to follow the "merge consecutive equal bits" convention that the legitimate encoder enforces. An attacker can place minimal-cost `U16(0)` values at "bit=1" (even) indices and maximal `U16(65535)` values at "bit=0" (odd) indices. Then:
- Every odd-indexed entry costs 3 bytes and contributes 65535 loop iterations that are always filtered out (bit=0), never advancing the `take(MAX_SLOTS)` counter.
- The entire vector must still be pulled through `flat_map`/`enumerate`/`filter` because Rust iterators are lazily pulled by the final `.collect()`, and `take()` sits downstream of the filter — it cannot short-circuit runs that never produce a passing item.

### Impact Explanation
With `RestartLastVotedForkSlots::MAX_BYTES = 824` [8](#0-7) , an attacker fits ≈274 such 3-byte entries (824/3), each contributing up to 65535 wasted iterations, for a total of ≈17.96 million loop iterations driven by a single sub-1KB CRDS value. The wire-to-compute ratio for N entries of `U16(65535)` is:

- Wire cost ≈ `3*N` bytes (via `wincode::serialized_size`)
- Total flat_map positions processed ≈ `N*65535`
- Ratio = `65535/3 ≈ 21,845x`

This confirms a genuine amplification on the order the question describes (up to ~65535x per individual run, ~21,845x aggregate per byte). Whether this constitutes a practical remote-exhaustion DoS depends on how frequently/broadly `to_slots` is invoked on ingested (not just self-authored) `RestartLastVotedForkSlots` values — I could only locate a single call site in `gossip/src/cluster_info.rs`, and did not have remaining budget to confirm whether it sits on the always-active push/pull ingestion path or is gated behind a manual wen-restart procedure. This materially affects severity: if `to_slots` is only called during an operator-initiated wen-restart window, the exposure window is narrow; if it's called for every ingested `RestartLastVotedForkSlots` CRDS value regardless of node state, this is a low-bandwidth, repeatable CPU-exhaustion vector against the gossip processing thread.

### Likelihood Explanation
Constructing the malicious payload requires no special privileges — `RestartLastVotedForkSlots` is a standard CRDS value type that any gossip peer can push/pull without stake or prior trust, and the `Sanitize` impl does not reject the crafted encoding. The only gate is the existing `MAX_CRDS_OBJECT_SIZE` wire-size check, which the attack respects by design (payload is well under the limit).

### Recommendation
- Bound `to_slots` traversal by total elements *scanned*, not just elements passing the filter — e.g., cap the cumulative `bit_count` sum (mirroring `num_encoded_slots()`) before iterating, or move a total-iteration `take()`/early-exit ahead of the filter stage.
- Validate `RunLengthEncoding` contents on deserialization/sanitize (e.g., reject if `num_encoded_slots()` exceeds `MAX_SLOTS` or an expected bound relative to wire size), closing the gap between the constructor's self-imposed invariant and what is actually accepted from untrusted peers.

### Proof of Concept
Conceptual (not executed, given tool constraints): build `RestartLastVotedForkSlots { offsets: SlotsOffsets::RunLengthEncoding(RunLengthEncoding(vec![U16(0), U16(65535)].repeat(137))) , ... }` directly (bypassing `RestartLastVotedForkSlots::new`/`RunLengthEncoding::new`), confirm `wincode::serialized_size` stays under `MAX_CRDS_OBJECT_SIZE`, then call `.to_slots(0)` and measure iteration count / wall-clock time, comparing against `N*65535` predicted iterations versus the `3*N`-byte wire cost.

**Note on confidence**: The core amplification logic in `RunLengthEncoding::to_slots` is confirmed directly from source. I was not able to fully verify the invocation context/frequency of `to_slots` in the live gossip ingest path within available tool budget — a Devin session with full repo access should trace `gossip/src/cluster_info.rs`'s single call site and any wen-restart module (not found in this index) to confirm reachability from unauthenticated gossip traffic before treating this as fully validated end-to-end.

### Citations

**File:** gossip/src/restart_crds_values.rs (L73-79)
```rust
#[cfg_attr(feature = "frozen-abi", derive(AbiExample, StableAbi, StableAbiSample))]
#[derive(Deserialize, Serialize, Clone, Debug, PartialEq, Eq, SchemaWrite, SchemaRead)]
struct U16(
    #[serde(with = "serde_varint")]
    #[wincode(with = "Leb128Int<u16>")]
    u16,
);
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

**File:** gossip/src/restart_crds_values.rs (L105-107)
```rust
impl RestartLastVotedForkSlots {
    // This number is MAX_CRDS_OBJECT_SIZE - empty serialized RestartLastVotedForkSlots.
    const MAX_BYTES: usize = 824;
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

**File:** gossip/src/restart_crds_values.rs (L210-212)
```rust
    fn num_encoded_slots(&self) -> usize {
        self.0.iter().map(|x| usize::from(x.0)).sum()
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
