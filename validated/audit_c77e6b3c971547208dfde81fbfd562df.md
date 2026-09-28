### Title
Unreachable `Participant` index causes panic (DoS) in PedPoP blame evaluation via `self.commitments[&sender]` - (File: crypto/dkg/pedpop/src/lib.rs)

### Summary
`BlameMachine::blame_internal` and `AdditionalBlameMachine::blame` accept caller-supplied `sender`/`recipient` `Participant` indexes and index `self.commitments` with them without membership checks. The `commitments` map is only populated for participants `1..=n`, but `Participant::new` accepts any nonzero `u16`. Feeding an accusation whose `sender` (or `recipient`) is outside `1..=n` — or whose inner commitments vector is shorter than `t` — causes an index-out-of-map / out-of-bounds panic, crashing the evaluating node. This mirrors CVE-2021-2065's class: attacker-controlled input reaches an unchecked code path causing a complete, repeatable crash (DoS-only impact, Medium).

### Finding Description
`AdditionalBlameMachine::new` builds `commitments` exclusively for `i in 1..=n` (pedpop/src/lib.rs:654-661). `blame()` forwards raw `sender`/`recipient` arguments into `blame_internal`, which evaluates `&self.commitments[&sender]` at line 599 inside `share_verification_statements`, and `decrypt_with_proof(sender, recipient, ...)` uses the participant indexes for ECDH context. There is no check that `sender`/`recipient` are valid participants in this DKG session.

Two distinct panic paths exist:

1. `self.commitments[&sender]` (line 599, and analogously line 490 in `calculate_share`) panics when `sender` is a constructible-but-not-enrolled `Participant` (e.g., `n + 1`). `Participant::new` only rejects zero, so `sender = Participant(0xffff)` passes any deserialization.
2. `AdditionalBlameMachine::new` inserts `encryption.register(i, msg).commitments` without enforcing `commitments.len() == t` (unlike `verify_r1` at line 317 which does enforce it). If blame is evaluated against a commitment vector with fewer than `t` elements, `commitments[t]` in the stripe loop (`calculate_share`, line 507) or `commitments[0]` access patterns panic with out-of-bounds.

The accusation inputs (`sender`, `recipient`, `EncryptedMessage` bytes, `EncryptionKeyProof`) are exactly the untrusted, deserializable data the API is designed to accept (`EncryptedMessage::read`, `Commitments::read` are the listed read targets), and `AdditionalBlameMachine` is explicitly documented for use by non-members evaluating blame.

### Impact Explanation
Any node that evaluates a blame accusation (a DKG participant or an independent blame evaluator) can be crashed by an accusation referencing a participant index not in `1..=n`, or built over a malformed commitment set. The panic aborts the process, matching the "hang or frequently repeatable crash (complete DOS)" impact class of the advisory — a single malformed accusation message reliably kills the evaluating party before it can identify blame, which also defeats the blame/attribution mechanism itself.

### Likelihood Explanation
The attacker only needs to submit an accusation tuple `(sender, recipient, msg, proof)` to a party running `blame()`/`AdditionalBlameMachine::blame`. `Participant` deserialization accepts any nonzero `u16`; no cryptographic validity is required to reach the indexing operation since the panic occurs before/independently of proof verification for the `commitments` lookup. This is a deterministic, single-message crash — trivially repeatable.

### Recommendation
Validate `sender` and `recipient` against the enrolled participant set (`1..=n`) at the top of `blame()`/`blame_internal` and return a defined fault result (e.g., blame the accuser or return an error) instead of indexing. In `AdditionalBlameMachine::new`, reject or skip commitment messages whose `commitments.len() != t`, mirroring the check in `verify_r1`. Replace `self.commitments[&sender]` with `.get(&sender)` and handle `None`.

### Proof of Concept
```rust
// Construct a blame evaluator over n=2 participants with valid commitment msgs
let mut blame = AdditionalBlameMachine::<Secp256k1>::new(context, 2, commitment_msgs).unwrap();
// Attacker submits an accusation with sender = Participant(3), constructible since
// Participant::new only rejects zero
let forged_sender = Participant::new(3).unwrap();
// Deserialize any EncryptedMessage::<C, SecretShare<C::F>> bytes as `msg`
blame.blame(forged_sender, Participant::new(1).unwrap(), msg, proof);
// -> panics at pedpop/src/lib.rs:599 on `self.commitments[&sender]` (index out of map)
```

Relevant code: `AdditionalBlameMachine::new` populating `commitments` only for `1..=n` at crypto/dkg/pedpop/src/lib.rs:656-660; unchecked index `&self.commitments[&sender]` at crypto/dkg/pedpop/src/lib.rs:599; the same unchecked index in `calculate_share` at line 490; missing `commitments.len() == t` check (contrast with crypto/dkg/pedpop/src/lib.rs:317).