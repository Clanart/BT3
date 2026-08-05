## Title
Overly Wide Future-Slot Acceptance Window in Turbine/Repair Shred Ingestion Enables Unauthenticated Remote Resource-Exhaustion - (File: `ledger/src/shred/filter.rs`)

### Summary
The external report flags `Engine.sol`'s `MAX_PRICE_DEVIATION_UPPER_BOUND = 500` (5%) as an unjustifiably wide tolerance band that lets otherwise-valid logic keep operating (minting) far outside a safe range, creating an exploitable window. The direct Agave analog is `max_shred_slot()` in `ledger/src/shred/filter.rs`, which defines the acceptance window for how far into the future an ingested shred's slot may be before the *cheap, pre-signature-verification* filter (`ShredFilterContext::should_discard_shred`) rejects it. Like the price-deviation bound, this is a single hard-coded tolerance constant that gates whether "clearly wrong" data is nevertheless accepted and processed further down a costly pipeline (deserialization, bank-aware limit lookups, blockstore/erasure-recovery bookkeeping).

### Finding Description
`max_shred_slot()` computes the upper bound for acceptable shred slots purely from the local root slot and the *current* epoch's slot count, with a floor of 500 slots: [1](#0-0) 

```
fn max_shred_slot(root: Slot, slots_per_epoch: Slot) -> Slot {
    const MAX_SHRED_DISTANCE_MINIMUM: Slot = 500;
    // Allow shreds up to half an epoch into the future to support catching up to the tip of the cluster.
    root.saturating_add(MAX_SHRED_DISTANCE_MINIMUM.max(slots_per_epoch / 2))
}
```

This bound is consumed by `should_discard_shred`, which is the *first* substantive check performed on every ingested shred, before signature verification, in both the packet-fetch fast path and the erasure-recovery path: [2](#0-1) 

```
let slot = match layout::get_slot(shred) {
    Some(slot) => {
        if slot > self.max_slot {
            self.stats.slot_out_of_range += 1;
            return true;
        }
        slot
    }
    ...
```

The window is deliberately generous — "half an epoch into the future" (with a 500-slot floor for short-epoch test clusters) — chosen only "empirically" for catch-up convenience, exactly analogous to the DeFi report's ±5% band chosen for "operational convenience" rather than safety. On mainnet-beta (432,000 slots/epoch), this means slots up to ~216,000 in the future — tens of hours ahead of the true root — are accepted through this stage without any stake-weighted or cryptographic validation at this point in the pipeline (`layout::get_version`/`get_slot`/`get_index` are unauthenticated header reads; signature checks happen later in `sigverify_shreds.rs`).

Critically, `should_discard_shred` is invoked from `ShredFetchStage::modify_packets` on **every UDP/QUIC packet arriving on the turbine/repair sockets**, which is a public, unauthenticated network surface — any remote unprivileged sender (not a staked validator, not a "malicious peer" with special trust) can address packets to a node's turbine port. Sending crafted shred headers with slot values within this wide but bogus future window causes the packets to pass this filter and proceed to more expensive downstream processing (`ShredLimitContext::shred_limits`, `get_erasure_config`, dedup/index bookkeeping, and — for shreds that pass sig-verify replay via spoofed/duplicated valid headers — insertion attempts in `Blockstore`), before ultimately being dropped only much later.

The CHANGELOG confirms this is a genuinely tightened-but-still-wide analog of the "5% is too high" pattern: previously up to **2 full epochs** ahead were accepted; this was reduced only to **half an epoch** — the fix pattern (narrow the tolerance) mirrors exactly what the DeFi auditors recommended (`≤100bps` instead of `500bps`), but the remaining half-epoch/500-slot band is still very large relative to the actual variance in legitimate catch-up scenarios, and is a known-fixed-but-still-generous constant, not something bounded by any adaptive/consensus-verified signal. [3](#0-2) 

### Impact Explanation
This falls under "non-RPC remote exhaustion/crash" from the valid-impact list: an unprivileged, unauthenticated remote sender can flood a validator's turbine/repair UDP or QUIC listener with packets carrying spoofed future-slot headers within the accepted (but still enormous) half-epoch/500-slot window, forcing the node to spend CPU/memory doing header parsing, bank-derived per-slot shred-limit lookups, FEC/erasure-config validation, and channel buffering for all of them before any cryptographic signature check rejects them. Because the check is slot-arithmetic only (`slot > self.max_slot`), it provides only coarse-grained resource-exhaustion protection, not protection tied to any verified consensus state (e.g., stake, signature, timing proof).

### Likelihood Explanation
Likelihood is elevated because:
- The shred-fetch path is reachable by any network peer capable of sending UDP/QUIC packets to a validator's public turbine/repair ports — no stake or trusted peer/validator identity is required to reach `should_discard_shred`.
- The check occurs before authentication (Merkle/Ed25519 signature verification happens later in `sigverify_shreds.rs`), so an attacker only needs to forge cheap, unauthenticated header fields (version, slot, index, fec_set_index) to pass this gate.
- The generous half-epoch/500-slot tolerance was already reduced once (from 2 epochs) precisely because the wider window was recognized as problematic, confirming the class of issue is real and previously exploited/observed, yet the current bound is still large in absolute slot-count terms.

### Recommendation
Tighten `max_shred_slot()`'s tolerance further and/or make future-slot acceptance conditional on cheaper but stronger evidence than raw slot arithmetic — e.g., bound it to a much smaller multiple of expected catch-up rate, add a per-source-IP/pubkey rate limiter ahead of the slot check, or require a lightweight PoH-consistency/signature pre-check before allocating per-packet processing work for far-future slots. Mirror the DeFi remediation pattern: reduce the "operational convenience" margin to the minimum empirically necessary, and treat "far future, unauthenticated" shreds as a stronger discard signal rather than a fixed generous band.

### Proof of Concept
1. From an arbitrary (non-staked, non-trusted) host, craft raw shred packets with:
   - valid `shred_version` (public/known value),
   - `slot` = `root + (slots_per_epoch/2 - 1)` (just inside the accepted window),
   - arbitrary/randomized `index`/`fec_set_index` fields within the type-specific bounds (`is_data_index_in_bounds`/`is_code_index_in_bounds`).
2. Send a high-rate flood of such packets to the target validator's turbine (data-plane) UDP/QUIC port(s).
3. Because `slot > self.max_slot` is false for all of these packets, `should_discard_shred` (see `ledger/src/shred/filter.rs:279-291`) allows every packet to proceed through the remaining checks (`shred_limits`, `get_erasure_config`, FEC alignment) — consuming CPU per packet — rather than being discarded at the very first, cheapest check.
4. Observe CPU/memory pressure on `ShredFetchStage`/`WindowService` processing threads scaling with the attacker's packet rate, independent of any stake or signature validity, since signature verification (`sigverify_shreds.rs`) occurs only after this filter stage.

**Note on confidence:** I could not fully verify downstream cost accounting (exact CPU cost per discarded packet, or whether other rate-limiters upstream of `ShredFetchStage` already mitigate this at line-rate) within the available index; a full assessment of exploitability would require examining `core/src/shred_fetch_stage.rs` packet-batch ingestion rate limits and QUIC/UDP socket-level throttling in more depth than the current search coverage allowed.

### Citations

**File:** ledger/src/shred/filter.rs (L279-291)
```rust
        let slot = match layout::get_slot(shred) {
            Some(slot) => {
                if slot > self.max_slot {
                    self.stats.slot_out_of_range += 1;
                    return true;
                }
                slot
            }
            None => {
                self.stats.slot_bad_deserialize += 1;
                return true;
            }
        };
```

**File:** ledger/src/shred/filter.rs (L416-426)
```rust
/// The maximum shred slot we allow for ingest given a current `root` slot.
fn max_shred_slot(root: Slot, slots_per_epoch: Slot) -> Slot {
    // When running with very short epochs (e.g. for testing), we want to avoid
    // filtering out shreds that we actually need. This value was chosen empirically
    // because it's large enough to protect against observed short epoch problems
    // while being small enough to keep the overhead small on deduper, blockstore,
    // etc.
    const MAX_SHRED_DISTANCE_MINIMUM: Slot = 500;
    // Allow shreds up to half an epoch into the future to support catching up to the tip of the cluster.
    root.saturating_add(MAX_SHRED_DISTANCE_MINIMUM.max(slots_per_epoch / 2))
}
```

**File:** CHANGELOG.md (L91-91)
```markdown
* Turbine shred ingestion now rejects shreds more than half an epoch in the future (previously up to 2 full epochs ahead was accepted).
```
