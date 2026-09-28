### Title
Attacker-controlled participant index panics `BlameMachine` / `AdditionalBlameMachine` during DKG fault attribution - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
The LibRaw bug class — an out-of-bounds access driven by attacker-controlled bytes parsed from an untrusted message — maps onto Serai as an unchecked participant index used as a `HashMap` key inside PedPoP's blame path. `BlameMachine::blame` and `AdditionalBlameMachine::blame` take `sender` and `recipient` `Participant` values (derived from an accusation message, e.g. the `accuser`/`faulty` u16 fields parsed off the wire) and index `self.commitments[&sender]` inside `blame_internal` without verifying the participant was part of the DKG set. A `Participant` outside `1..=n` (only 0 is rejected by `Participant::new`) causes an out-of-bounds map lookup that panics, crashing the party performing blame attribution.

### Finding Description
`blame_internal` indexes the commitments map with the attacker-supplied `sender` index: `&self.commitments[&sender]` (pedpop/src/lib.rs:599). The commitments map is only populated for `1..=n`: in `KeyMachine::calculate_share` it is built from `verify_r1`'s per-participant commitments (pedpop/src/lib.rs:331), and in `AdditionalBlameMachine::new` it is populated exclusively by `for i in 1 ..= n` (pedpop/src/lib.rs:656-660). Neither `blame` entry point (pedpop/src/lib.rs:623-633, 674-683) validates `sender`/`recipient` against `params`/`n` before use. `Participant::new` only rejects `0` (dkg/src/lib.rs:29-35), so any non-zero u16 — including indexes greater than `n` — parses successfully from the accusation's two-byte fields and reaches the indexing operation. `std::collections::HashMap`'s `Index` impl panics on a missing key, turning the malformed index into a hard panic.

### Impact Explanation
A participant submitting a blame/accusation message naming a `sender` (or `faulty`) index `> n` crashes every honest party that evaluates the accusation, aborting DKG fault attribution and potentially the key-generation/signing session. This matches the report's impact shape exactly (CVSS C:N/I:N/A:H): pure availability loss triggered by a crafted input. Because blame evaluation is how honest nodes attribute faults, crashing it also prevents removal of the genuinely faulty participant, compounding the denial of service.

### Likelihood Explanation
The accusation fields are a two-byte `Participant` read from a serialized message; setting them to `n+1` (or any absent index) is trivial. The panic requires only that the blame path be exercised, which is the normal protocol response to any reported fault — no cryptographic assumption, timing, or privileged position is needed beyond being able to send a blame-triggering message.

### Recommendation
Validate `sender` and `recipient` at the top of `blame` / `blame_internal` (e.g. `u16::from(sender) <= n` and map membership via `get`), returning a defined error or treating the accuser as faulty instead of indexing directly. Apply the same bounds check wherever `Participant` values parsed from messages are used as `HashMap` keys (`self.commitments[&sender]` at pedpop/src/lib.rs:599, and analogous lookups).

### Proof of Concept
1. Construct `AdditionalBlameMachine::new(context, n, commitment_msgs)` for a DKG with `n = 3`, supplying valid commitment messages for participants 1, 2, 3.
2. Call `.blame(sender, recipient, msg, proof)` with `sender = Participant::new(4).unwrap()` (parses fine; only 0 is rejected) and any well-formed `msg`/`proof`.
3. Execution reaches `self.commitments[&sender]` in `blame_internal`; the map only holds keys 1..=3, so the `Index` impl panics with "key not found", aborting the caller instead of returning a `Participant` verdict.

Note: reachability depends on the surrounding protocol feeding wire-parsed `accuser`/`faulty` participant fields into `blame`/`AdditionalBlameMachine::blame`. I verified the missing bounds check in `crypto/dkg/pedpop/src/lib.rs` but did not fully trace the coordinator/processor call sites, and the panic itself (not memory corruption) is the Rust-appropriate analog of the LibRaw OOB write.