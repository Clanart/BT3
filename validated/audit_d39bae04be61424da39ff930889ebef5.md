### Title
Missing lower-bound validation on participant index causes reachable panic in FROST `sign` - (File: crypto/frost/src/sign.rs)

### Summary
The Open5GS CVE-2023-37006 class — a remotely-supplied protocol message missing/failing a required field check that trips an assertion and crashes the process — maps onto `AlgorithmSignMachine::sign` in modular-frost. The signing-set validation checks the *maximum* participant index (`<= n`), duplicates, and quantity, but never rejects `Participant(0)`. An included index of `0` reaches `self.params.keys.view(included.clone()).unwrap()`, where `ThresholdView` construction fails for the invalid index and the `unwrap()` panics, crashing the signer instead of returning a blameable `FrostError`.

### Finding Description
In `crypto/frost/src/sign.rs`, `sign` builds the `included` set from the keys of the caller-supplied `preprocesses: HashMap<Participant, Preprocess>` map:

```rust
// crypto/frost/src/sign.rs:297-313
if included.len() < usize::from(multisig_params.t()) { ... }
if u16::from(included[included.len() - 1]) > multisig_params.n() { ... }   // upper bound only
for i in 0 .. (included.len() - 1) {
  if included[i] == included[i + 1] { ... }                                 // duplicates only
}
let view = self.params.keys.view(included.clone()).unwrap();               // panics on Participant(0)
```

`Participant` is a `u16` newtype whose zero value is invalid (`Participant::new` rejects `0`, and valid participants are `1 ..= n`), but nothing in `sign` enforces `included[0] != 0`. The check at line 302 only bounds the largest element. `ThresholdKeys::view`/`ThresholdView::new` validates the participant set (interpolation over `1 ..= n` is impossible for index `0`), returns `Err`, and the unconditional `.unwrap()` turns a malformed signing set into a process abort.

The byte-level reachability exists through the sanctioned surface: `Participant` is deserialized as a bare `u16` map key in the coordinator/processor preprocess-distribution messages (a SCALE-decoded `HashMap<Participant, Vec<u8>>` does not run `Participant::new` validation), and each value is fed to `read_preprocess` before `sign` is called with the map. A peer participant who publishes their preprocess under index `0`, or a preprocess set containing a `0` key, causes every honest signer processing the batch to panic inside `sign` — where the code even asserts this class of failure is impossible: the processor maps `FrostError::InvalidParticipant`/`InvalidSigningSet` to `unreachable!()`, so the panic propagates uncaught (see `processor/src/slash_report_signer.rs:191-197`, where only `InvalidPreprocess`/`InvalidShare` are considered survivable).

### Impact Explanation
A single malformed preprocess map entry crashes the signing participant during `sign`. Repeated across retry attempts, this is a persistent denial of service against threshold signing (and any downstream cosigning/signing pipeline that feeds the machine), matching the CVE's repeated-crash DoS impact. Because the crash occurs before share production, no secret material is leaked, but liveness of the multisig is destroyed for as long as the malformed entry is replayed.

### Likelihood Explanation
All checks that *do* exist (upper bound, duplicates, count) show the code intends to sanitize `included`; the missing zero check is a straightforward gap. Triggering requires only that an attacker-influenced preprocess map contain key `Participant(0)` — encodable whenever `Participant` is decoded from wire bytes without re-validation (raw `u16` = `0`). No key material, collusion, or protocol-round control beyond publishing a keyed preprocess is needed. Medium severity is appropriate: it is a reliable, input-triggered crash but confined to signing liveness.

### Recommendation
Before calling `keys.view(included)`, validate `included[0]` is non-zero (e.g. `if included[0].0 == 0 { Err(FrostError::InvalidParticipant(...)) }`), or replace `.unwrap()` with a `?`-propagated `FrostError` so a malformed signing set degrades to a blameable error instead of a panic. Ideally, make `Participant`'s deserializer run `Participant::new` so `0` is unrepresentable post-decode.

### Proof of Concept
```rust
// Requires: ThresholdKeys<C> for a 2-of-2 (or any t/n) set for participant i = 1.
// Construct a preprocess map containing a preprocess keyed under Participant(0)
// alongside a valid preprocess so `included.len() >= t` and the max-index check passes.

let mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>> = HashMap::new();
preprocesses.insert(Participant(0), attacker_preprocess_for_index_0); // parsed bytes OK
preprocesses.insert(Participant(2), honest_preprocess);               // keeps last() <= n

// included = [0, 1(local), 2] -> passes len, upper-bound, and duplicate checks
// sign() then executes:
//   let view = self.params.keys.view(included.clone()).unwrap();
// view() errors on the invalid 0 index -> unwrap() panics -> process aborts.
let _ = machine.sign(preprocesses, b"msg"); // panic, not FrostError
```

Reachability note/caveat: the wire path depends on `Participant` being decoded as a raw `u16` (bypassing `Participant::new`) when preprocess maps are deserialized from coordinator/P2P messages — if `Participant`'s `Decode` impl were manually validated, the panic would be unreachable from bytes; the derived/unvalidated decode path used for preprocess maps is what makes index `0` injectable.