### Title
Missing membership check before HashMap indexing causes panic on out-of-range participant index in PedPoP blame path - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The kernel bug class is "value returned by a fallible lookup used without a NULL check." The Rust analog is a `HashMap` indexed with the `[]` operator (which panics on a missing key) where the key is derived from attacker-influenced `Participant` values. In `Decryption::decrypt_with_proof`, the `decryptor` participant is used to index `self.enc_keys` without checking membership, and in `BlameMachine::blame_internal` the `sender` is used to index `self.commitments`. Both panic if the supplied `Participant` is not one of the `1 ..= n` registered parties.

### Finding Description
`Decryption::new` only populates `enc_keys` via `register`, which is called once per participant `i` in `1 ..= n` inside `AdditionalBlameMachine::new` (crypto/dkg/pedpop/src/lib.rs:656-660) or during `verify_r1` for the local key machine. There is no entry for any other index.

`decrypt_with_proof` then performs `self.enc_keys[&decryptor]` unconditionally (crypto/dkg/pedpop/src/encryption.rs:388). `HashMap`'s `Index` impl panics when the key is absent — the exact equivalent of dereferencing a `NULL` returned by a lookup without checking it.

The reachable path is the public blame API:

- `AdditionalBlameMachine::blame(sender, recipient, msg, proof)` → `BlameMachine::blame_internal` → `decrypt_with_proof(from = sender, decryptor = recipient, msg, proof)` → `self.enc_keys[&recipient]` (crypto/dkg/pedpop/src/lib.rs:674-682, 582; encryption.rs:388).
- If `msg.pop.verify` passes (the sender can trivially make this happen by supplying a valid PoP for their own key, since `encrypt` produces valid PoPs over the ciphertext) and `proof` is `Some`, the unchecked indexing fires.
- Independently, `blame_internal` indexes `self.commitments[&sender]` (crypto/dkg/pedpop/src/lib.rs:599) with no prior check that `sender` is within `1 ..= n`, so an out-of-range `sender` also panics once decryption and share parsing succeed.

`Participant::new` only rejects zero (crypto/dkg/src/lib.rs:600 shows it as an `Option`-returning constructor), so any `u16` value `> n` or otherwise unregistered is accepted upstream and passed straight into these lookups. `AdditionalBlameMachine` is explicitly constructed for use by parties who were not members of the DKG (crypto/dkg/pedpop/src/lib.rs:639-648), and its `blame` arguments — `sender`, `recipient`, the `EncryptedMessage`, and the optional `EncryptionKeyProof` — are deserialized, attacker-influenced protocol data (`EncryptedMessage::read` / `EncryptionKeyProof::read`, encryption.rs:171-177, 267-269). The blame functions' docs require the *message* be authenticated, but do not document that `sender`/`recipient` must be members of `1 ..= n`, so this is not a documented-MUST-misuse case.

### Impact Explanation
An unprivileged party who can submit or relay a blame accusation (or feed `sender`/`recipient`/`msg`/`proof` to a host calling `BlameMachine::blame` or `AdditionalBlameMachine::blame`) can crash the evaluating node by naming a `recipient` or `sender` participant index outside the registered set. This is a denial of service against the blame/arbitration path — the same availability impact as the NULL-pointer dereference in the reference CVE — and it additionally prevents legitimate blame resolution from completing, stalling the DKG abort/slashing flow.

### Likelihood Explanation
Triggering requires control over the `sender`/`recipient` `Participant` values passed to `blame`, plus a `msg` whose PoP verifies (trivial for a message the attacker constructs honestly) and a `proof` to reach the `enc_keys` lookup — or, for the `commitments[&sender]` panic, a well-formed encrypted share body. Whether a downstream integrator bounds these indexes before calling is implementation-defined; the library itself performs no validation, so any caller that forwards on-wire participant identifiers is exposed.

### Recommendation
Replace the panicking `HashMap` index with a checked lookup in both places:

- In `Decryption::decrypt_with_proof` (encryption.rs:388), use `self.enc_keys.get(&decryptor)` and return a `DecryptionError` (e.g., `InvalidProof` or a new variant) when absent.
- In `BlameMachine::blame_internal` (crypto/dkg/pedpop/src/lib.rs:599), check `self.commitments.get(&sender)` (and validate `recipient`/`sender` against `1 ..= n` at the top of `blame`/`blame_internal`) before indexing, treating an unknown participant as fault of the party that named them rather than a panic.

### Proof of Concept
```rust
// Build an AdditionalBlameMachine for a DKG with n participants.
let n: u16 = 3;
// ... collect `commitment_msgs` for participants 1..=n ...
let machine = AdditionalBlameMachine::<C>::new(context, n, commitment_msgs).unwrap();

// A validly-formed EncryptedMessage whose PoP verifies is obtainable by running
// `encrypt` (or replaying any real share message), and a valid EncryptionKeyProof
// via `EncryptionKeyProof::read` on proof bytes.
let sender    = Participant::new(1).unwrap();      // in-range sender
let recipient = Participant::new(n + 1).unwrap();  // NOT a registered participant

// Panics at encryption.rs:388 on `self.enc_keys[&decryptor]`
machine.blame(sender, recipient, msg, Some(proof));

// Symmetrically, sender = Participant::new(n + 1) with a decryptable msg
// panics at pedpop/src/lib.rs:599 on `self.commitments[&sender]`.
```

Caveats: I could not fully trace whether the production caller (e.g., `VerifyBlame` handling) bounds `accuser`/`accused` to `1 ..= n` before reaching this API; the vulnerability statement stands on the library's own unchecked indexing of caller-supplied `Participant` values, which are unauthenticated scalar inputs rather than validated set members.