### Title
Unvalidated `sender`/`recipient` indexes in PedPoP blame evaluation cause a panic reachable from untrusted blame inputs - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The Checkmk CVE-2025-39666 pattern is "untrusted, attacker-controlled data processed by a more-privileged routine." In Serai's PedPoP DKG, the analogous surface is the blame-evaluator: `BlameMachine::blame` / `AdditionalBlameMachine::blame` accept attacker-chosen `sender`/`recipient` `Participant` values plus attacker-controlled `EncryptedMessage`/`EncryptionKeyProof` bytes (via `EncryptedMessage::read` / `EncryptionKeyProof::read`), and the decryption path indexes internal maps with those unvalidated indexes, causing a panic.

### Finding Description
`Decryption::decrypt_with_proof` resolves the decryptor's registered encryption key with a direct `HashMap` index:

```rust
// crypto/dkg/pedpop/src/encryption.rs
&[self.enc_keys[&decryptor], *proof.key],
```

`enc_keys` only contains entries for the `n` DKG participants, registered either by `Decryption::register` during `verify_r1` or by `AdditionalBlameMachine::new` for `i in 1 ..= n` (crypto/dkg/pedpop/src/lib.rs:656-660). `Participant::new` accepts any non-zero `u16`, so `decryptor = Participant(n + k)` or any participant removed/not-registered yields `enc_keys[&decryptor]` panicking (`HashMap` index on absent key).

The same defect exists in `blame_internal` for the sender side: `self.commitments[&sender]` (crypto/dkg/pedpop/src/lib.rs:597-601) is indexed with a caller-supplied `sender` that is never range-checked against `n`.

Neither `BlameMachine::blame` nor `AdditionalBlameMachine::blame` bounds-check `sender` or `recipient` before calling `blame_internal` → `decrypt_with_proof`. `AdditionalBlameMachine` is explicitly designed to evaluate blame "regardless of if the caller was a member in the DKG protocol" (doc comment at lib.rs:639-648), so these parameters are untrusted inputs, and `msg` is itself deserialized untrusted bytes via `EncryptedMessage::read` (encryption.rs:171-177).

### Impact Explanation
Any party that can submit a blame evaluation request — including a non-participant accuser via `AdditionalBlameMachine::blame` — can crash the evaluating process by naming a `recipient`/`sender` outside `1 ..= n`. In deployments where blame adjudication runs in a coordinator, arbitrator, or node process (the privileged consumer of attacker-supplied messages, mirroring the `omd`-as-root shape of the CVE), this is a remotely triggered panic: it prevents fault attribution, can abort the surrounding protocol handler, and gives a cheap, repeatable DoS primitive that also lets a genuinely faulty participant stall accountability. An abort/panic in the blame path forces DKG failure with no attributable party, which the protocol design (blame proofs) is specifically meant to avoid.

### Likelihood Explanation
Reachability is direct: all inputs (`sender`, `recipient`, `msg`, `proof`) are caller-controlled, `Participant::new` only rejects zero, and no check compares the indexes against `params.n()` or map membership before indexing. The only precondition is that a blame/accusation flow is exercised, which is precisely the path taken when a (potentially malicious) DKG participant misbehaves — i.e., exactly when this code must be robust. No cryptographic break or collusion is required; a single malformed accusation suffices.

### Recommendation
In `decrypt_with_proof` and `blame_internal`, replace direct indexing with fallible lookups and return blame/`DecryptionError` instead of panicking:

- `self.enc_keys.get(&decryptor)` → treat `None` as the accuser/attacker being faulty (or a dedicated `InvalidParticipant` error) rather than `DecryptionError::InvalidProof`, so a bad `recipient` blames the accuser, not a registered party.
- `self.commitments.get(&sender)` → same for an out-of-range `sender`.
- Optionally, pre-validate `u16::from(sender) <= n` and `u16::from(recipient) <= n` in `BlameMachine::blame` / `AdditionalBlameMachine::blame` before any work, since `n` is known (`AdditionalBlameMachine::new` takes `n`).

### Proof of Concept
```rust
// Given a completed/registered blame machine for a DKG with n participants:
let mut blame = AdditionalBlameMachine::<Ristretto>::new(context, n, commitment_msgs).unwrap();

// Attacker-controlled accusation: recipient index outside 1 ..= n
let evil_recipient = Participant::new(n + 1).unwrap(); // valid Participant, not a member
let msg_bytes: Vec<u8> = /* attacker-chosen EncryptedMessage bytes */;
let msg = EncryptedMessage::<Ristretto, SecretShare<_>>::read(&mut msg_bytes.as_slice(), params)?;
let proof = EncryptionKeyProof::read(&mut proof_bytes.as_slice()).ok();

// Panics inside decrypt_with_proof at `self.enc_keys[&decryptor]`
blame.blame(sender, evil_recipient, msg, proof);
```

Likewise, `blame.blame(Participant::new(n + 7).unwrap(), honest_recipient, msg, proof)` panics at `self.commitments[&sender]` in `blame_internal` when the share reaches share-verification — and earlier, at `self.enc_keys[&decryptor]` if `proof` is `Some`.

Note on scope/limitations: I verified this panic path against `crypto/dkg/pedpop/src/{lib.rs,encryption.rs}` directly. I did not complete inspection of `networks/bitcoin/src/wallet/` scanner internals (coinbase maturity, `register_offset` collisions) or the `musig`/key-recovery paths within the available budget, so additional analogs of this bug class may exist there.