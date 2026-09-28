### Title
Unauthenticated panic in PedPoP blame evaluation via out-of-range `sender`/`recipient` indexes - (File: crypto/dkg/pedpop/src/lib.rs, crypto/dkg/pedpop/src/encryption.rs)

### Summary
The PedPoP DKG blame API indexes `HashMap`s with attacker-supplied `Participant` values without checking they are within `1 ..= n`. A party submitting a blame request naming a `sender` or `recipient` outside the registered set triggers a panic (`HashMap` index on missing key), crashing the evaluator — a remotely reachable denial of service analogous to CVE-2017-3331's reachable-crash class.

### Finding Description
`BlameMachine::blame` / `AdditionalBlameMachine::blame` accept `sender`, `recipient`, an `EncryptedMessage`, and an optional `EncryptionKeyProof` — all supplied by an accusing party. Both funnel into `blame_internal`, which calls `Decryption::decrypt_with_proof` and then indexes `self.commitments[&sender]` (crypto/dkg/pedpop/src/lib.rs:599). Inside `decrypt_with_proof`, `self.enc_keys[&decryptor]` is indexed when a proof is provided (crypto/dkg/pedpop/src/encryption.rs:388). Neither map lookup is preceded by a bounds/membership check.

`Participant` accepts any non-zero `u16` (crypto/dkg/src/lib.rs:29-35), so an accuser can pass `Participant(0xFFFF)` or any index `> n` (e.g., n = 150). `enc_keys`/`commitments` are only populated for `1 ..= n` (`AdditionalBlameMachine::new` registers exactly `1 ..= n`, crypto/dkg/pedpop/src/lib.rs:656-660), so indexing with an out-of-range participant panics.

Additionally, in `KeyMachine::calculate_share`, `self.commitments[&l]` (lib.rs:490) is safe only because `validate_map` constrains `l` to `all_participant_indexes`; but `blame`/`decrypt_with_proof` perform no equivalent validation of `sender`/`recipient`, despite taking them directly from the accuser's message.

### Impact Explanation
A single blame request with an out-of-range `sender` (reaching `self.commitments[&sender]`) or `recipient` (reaching `self.enc_keys[&decryptor]`, when accompanied by any `EncryptionKeyProof` since the proof verification indexes the map before checking the DLEq) panics the evaluating process. For validators/processors that run blame evaluation as part of DKG fault handling, this is a remotely triggerable crash — complete loss of availability of that node, matching the availability-only impact of the reference CVE. No secret is needed; the accusing party only needs to reach the blame-evaluation path with public inputs.

### Likelihood Explanation
Blame messages are part of the DKG abort path and are consumed from other participants. Any participant (or, via `AdditionalBlameMachine::new`, potentially a non-participant evaluator fed accusation data) can craft `sender`/`recipient` values. `Participant::new` is `pub const` and only rejects zero, so no deserialization barrier exists. Triggering requires only submitting one malformed blame request — trivially repeatable.

### Recommendation
Validate `sender` and `recipient` against the registered participant set (`1 ..= n` and present in `commitments`/`enc_keys`) at the top of `blame_internal` and `decrypt_with_proof`, returning an error or a well-defined blame result (e.g., blame the accuser) instead of panicking. Replace `map[&key]` indexing with `.get(&key)` plus explicit handling.

### Proof of Concept
```rust
// Setup: complete a PedPoP DKG (or build AdditionalBlameMachine) for n = 3 participants.
let params = ThresholdParams::new(2, 3, Participant::new(1).unwrap()).unwrap();
// ... run generate_coefficients / generate_secret_shares / calculate_share to obtain
// a BlameMachine, or construct AdditionalBlameMachine::new(context, 3, commitment_msgs) ...

// Attacker-controlled accusation naming a participant index outside 1..=n:
let attacker_recipient = Participant::new(0xFFFF).unwrap();
let msg: EncryptedMessage<C, SecretShare<F>> = /* any EncryptedMessage, e.g. an honest one */;
let proof = Some(any_encryption_key_proof); // any EncryptionKeyProof, even invalid

// Panics at crypto/dkg/pedpop/src/encryption.rs:388 on `self.enc_keys[&decryptor]`
// (indexing a HashMap with a key that was never registered for 1..=n):
let faulty = blame_machine.blame(sender, attacker_recipient, msg, proof);
// Similarly, sender = Participant::new(0xFFFF) panics at lib.rs:599 on
// `self.commitments[&sender]` once the signature check passes or earlier paths allow it.
```

The panic is a `HashMap` index on a missing key — an unconditional abort of the calling thread, converting a single malformed blame request into a node-level denial of service.