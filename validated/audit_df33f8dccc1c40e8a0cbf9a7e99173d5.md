### Title
Unauthenticated panic in PedPoP blame evaluation via unregistered participant indexes - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2026-21496 is a NULL-pointer dereference triggered while parsing an attacker-controlled signature, yielding a crash-only (availability) bug. The Serai analog is not a literal NULL dereference (Rust prevents that) but the identical class and impact: attacker-controlled `Participant` indexes fed to the PedPoP blame API are used as unchecked `HashMap` keys, causing an indexing panic that aborts the blame evaluation — a reachable, unprivileged denial of service during DKG fault resolution.

### Finding Description
`Decryption::decrypt_with_proof` resolves the decryptor's encryption key by direct `HashMap` indexing:

```rust
// crypto/dkg/pedpop/src/encryption.rs:388
&[self.enc_keys[&decryptor], *proof.key],
```

`enc_keys` is only populated for participants `1..=n` — either via `Encryption::register` inside `verify_r1` (crypto/dkg/pedpop/src/lib.rs:315) or via `AdditionalBlameMachine::new` (lib.rs:656-660). Both `BlameMachine::blame` (lib.rs:623) and `AdditionalBlameMachine::blame` (lib.rs:674) take `sender`/`recipient` as caller-supplied `Participant` values with no bounds check against the DKG's `n`. Passing `proof: Some(_)` with a `recipient` outside `1..=n` reaches `self.enc_keys[&decryptor]`, which panics on the missing key.

A second panic of the same shape exists one step later: after the message's PoP verifies and the decrypted bytes parse as a scalar, `blame_internal` evaluates `self.commitments[&sender]` (lib.rs:599). `commitments` is likewise keyed only over `1..=n`, so an accusation naming a `sender` index that was never a participant panics after the attacker supplies a syntactically valid `EncryptedMessage` — which any party can construct, since `pop_challenge` merely transcripts the `from` field and the sender signs their own `key`/`msg` (encryption.rs:302-324, `EncryptedMessage::read` at encryption.rs:171 accepts arbitrary bytes for `key`, `pop`, and `msg`).

The required inputs — a `Participant` pair, an `EncryptedMessage`, and an `EncryptionKeyProof` — are exactly the public, authenticated-channel messages an ordinary DKG participant (or anyone invoking `AdditionalBlameMachine`, which explicitly supports non-member blame arbitration, lib.rs:639-648) supplies. No threshold control, leaked keys, or malicious validator set is needed; only the ability to submit a blame accusation.

### Impact Explanation
Like the iccDEV NULL dereference, the consequence is availability-only: a single crafted accusation crashes the process evaluating blame. In Serai deployments this is worse than a lone thread panic — binaries install panic hooks that `exit(1)` on any task panic (e.g., relayer `main.rs`), and `BlameMachine`/`AdditionalBlameMachine::blame` return `Participant`, not `Result`, so the panic cannot be handled by callers. A faulty participant who is about to be blamed can submit a malformed counter-accusation first, crashing the honest parties' blame arbitration and stalling DKG abort/eviction, or repeatedly crash third-party blame evaluators.

### Likelihood Explanation
Triggering requires only that the local node run the blame path (the documented recovery flow for invalid DKG shares — `PedPoPError::InvalidShare` carries an `EncryptionKeyProof` meant to be fed into `blame`). An attacker who is a DKG participant, or who can submit accusations to a blame-evaluating service, chooses `sender`/`recipient` freely; nothing in `AdditionalBlameMachine::blame` constrains them to `1..=n`. The cost is one valid or even semi-valid `EncryptedMessage` plus a well-formed `EncryptionKeyProof`. It is deterministic — a single invocation panics.

### Recommendation
Replace the indexing lookups with checked access in `Decryption::decrypt_with_proof` and `blame_internal`: use `self.enc_keys.get(&decryptor)` / `self.commitments.get(&sender)` and return a `DecryptionError`/`PedPoPError` (or treat the accuser as faulty) when the participant was never registered. Additionally, validate `sender` and `recipient` against `1..=n` at the top of `BlameMachine::blame` and `AdditionalBlameMachine::blame` before touching the maps.

### Proof of Concept
Conceptual trigger against `AdditionalBlameMachine` (non-member blame evaluator):

```rust
// Setup: context, n = 3, commitment_msgs for participants 1..=3
let machine = AdditionalBlameMachine::<Ed25519>::new(context, 3, msgs).unwrap();

// Attacker-controlled accusation naming a recipient outside 1..=n
let recipient = Participant::new(4).unwrap(); // valid Participant, never registered
let msg: EncryptedMessage<_, SecretShare<_>> = EncryptedMessage::read(&mut bytes, params)?;
let proof: EncryptionKeyProof<_> = EncryptionKeyProof::read(&mut proof_bytes)?;

// Panics at encryption.rs:388 — self.enc_keys[&Participant(4)] on empty slot
machine.blame(Participant::new(1).unwrap(), recipient, msg, Some(proof));
```

Alternatively, with `recipient` registered but `sender = Participant::new(4)`, supply an `EncryptedMessage` whose `pop` verifies under `from = 4` (the sender field is only a transcript input the attacker signs for) and whose payload decodes to a valid scalar — execution then panics at `self.commitments[&sender]` (lib.rs:599).

Confidence note: panic-on-blame is reachable in `decrypt_with_proof` whenever `proof` is `Some` and `decryptor` is unregistered (encryption.rs:381-388), and at lib.rs:599 for an unregistered `sender` once the prior checks pass. Both sites index maps keyed strictly over `1..=n` while the public `blame` APIs accept arbitrary `Participant` values — verified in the shown code.