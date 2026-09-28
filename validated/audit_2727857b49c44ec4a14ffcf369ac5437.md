### Title
Unconditional `HashMap` index on attacker-chosen `sender` panics in PedPoP blame evaluation - ([File: crypto/dkg/pedpop/src/lib.rs](crypto/dkg/pedpop/src/lib.rs))

### Summary
`BlameMachine::blame` / `AdditionalBlameMachine::blame` unconditionally index `self.commitments[&sender]` inside `blame_internal`, without checking that `sender` is a participant whose commitments were registered. This is the same bug class as CVE-2022-49569: an optional/keyed resource is unconditionally dereferenced without first validating it exists, producing a panic (Rust's NULL-deref analog) reachable from untrusted input.

### Finding Description
In `blame_internal`, after decrypting the accused message, the code runs:

```rust
multiexp_vartime(&share_verification_statements::<C>(
  recipient,
  &self.commitments[&sender],
  ...
))
```

`self.commitments` only contains entries for participants `1..=n` whose commitment messages were registered (`AdditionalBlameMachine::new` inserts exactly `commitments.insert(i, ...)` for `i in 1..=n`, and `KeyMachine` carries the validated per-participant map). However, `blame()` takes `sender: Participant` as an unconstrained public argument — any non-zero `u16`. An accuser (or any party invoking blame evaluation) can pass a `sender` index `> n` or one whose commitments were never registered, causing `self.commitments[&sender]` to panic on the missing key.

The analogous pattern also exists for `recipient`-side paths and in `KeyMachine::calculate_share` at `self.commitments[&l]` — though there `l` is constrained by `validate_map` against `all_participant_indexes()`, the `blame` path has no such check on `sender`.

### Impact Explanation
Any party able to trigger blame evaluation (a DKG participant submitting an accusation, or an external observer calling `AdditionalBlameMachine::blame`) can crash the evaluating process by supplying a `sender` index outside the committed set. The blame protocol is the DKG's fault-resolution path; crashing it aborts key generation and prevents fault attribution — a denial of service on the same order as the referenced kernel NULL-deref (availability impact, CVSS A:H). The required input — a `Participant` index plus an `EncryptedMessage` (readable via `EncryptedMessage::read`) — is fully attacker-controlled.

### Likelihood Explanation
The `sender` parameter is a plain `Participant` (any non-zero `u16`) with no bounds check against `params.n()` or the `commitments` map. No authentication restricts who may call `blame` — `AdditionalBlameMachine` is explicitly designed for non-participant blame evaluation. Triggering the panic requires only supplying a syntactically valid `EncryptedMessage` for a non-existent sender index that survives to the share-verification branch (or simply a valid decryption path).

### Recommendation
Before indexing, check membership: `let Some(sender_commitments) = self.commitments.get(&sender) else { return sender /* or an explicit InvalidParticipant error */ };`. Apply the same to `recipient` where used. Alternatively, validate `sender`/`recipient` against `all_participant_indexes()` at the top of `blame_internal`.

### Proof of Concept
```rust
// Setup: an n=2 DKG completes; commitments registered for Participants 1,2.
let machine = AdditionalBlameMachine::<Secp256k1>::new(context, 2, commitment_msgs).unwrap();
// Attacker supplies blame against a non-existent sender index
let bogus_sender = Participant::new(3).unwrap();
// msg: any EncryptedMessage<C, SecretShare<F>> that decrypts/decodes far enough,
// e.g. a validly-formed message replayed from a real sender
machine.blame(bogus_sender, recipient, msg, proof);
// -> panic: `self.commitments[&sender]` on missing key at lib.rs:599
```
The panic occurs at `self.commitments[&sender]` inside `share_verification_statements` invocation in `blame_internal` (crypto/dkg/pedpop/src/lib.rs, ~line 597-599), reached whenever the decryption/proof path does not return early.