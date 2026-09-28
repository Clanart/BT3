### Title
Missing-key panic in `Decryption::decrypt_with_proof` reachable via blame verification allows unauthenticated crash (DoS) - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
JLSEC-2026-840 describes an out-of-bounds/stale-state read in ImageMagick's `SetImageAlphaChannel()` path producing a denial of service when an attacker supplies a malicious file. The analogous class in Serai is a state-dependent lookup that panics on attacker-influenced input. In `crypto/dkg/pedpop/src/encryption.rs`, `Decryption::decrypt_with_proof` indexes `self.enc_keys[&decryptor]` with a `HashMap` key (`Participant`) that is not guaranteed to be present, causing a panic — a reachable crash / DoS analogous to the upstream out-of-bounds read.

### Finding Description
`Decryption::register` populates `enc_keys` only for participants whose `EncryptionKeyMessage` was actually registered:

- `crypto/dkg/pedpop/src/encryption.rs:351-362` — `register` inserts `participant -> msg.enc_key` and asserts no re-registration.
- `crypto/dkg/pedpop/src/encryption.rs:366-397` — `decrypt_with_proof(from, decryptor, msg, proof)` first verifies `msg.pop` (a Schnorr PoP bound to `from`, `msg.key`, and the ciphertext bytes), then, when `proof` is `Some`, evaluates `self.enc_keys[&decryptor]` as the second base for the DLEq proof:

```rust
proof.dleq.verify(
  &mut encryption_key_transcript(self.context),
  &[C::generator(), msg.key],
  &[self.enc_keys[&decryptor], *proof.key],
)
```

`HashMap`'s `Index` impl panics when `decryptor` is absent. `decryptor` is a `Participant` value supplied from protocol messages (the accuser index in blame handling), while `enc_keys` only contains entries for participants whose commitment/encryption-key messages were registered by `verify_r1` / `AdditionalBlameMachine`. There is no check that `decryptor` was registered before indexing.

The upstream bug shape is preserved: state (the pixel cache / the `enc_keys` map) is populated under one set of assumptions, and a later read (`GetPixelRed` / `enc_keys[&decryptor]`) is performed on data outside that populated state, producing memory-unsafe behavior (there a heap over-read, here a panic — both availability-only).

### Impact Explanation
An out-of-range or unregistered `decryptor` index reaching `decrypt_with_proof` with a `Some(proof)` argument panics the thread performing blame verification. In the processor's blame-verification path (`processor/src/key_gen.rs`, `CoordinatorMessage::VerifyBlame` → `AdditionalBlameMachine::new(...).blame(accuser, accused, share, blame)`), the `accuser`/`accused`/`share`/`blame` fields are taken from a tributary message. If `accuser` resolves to a `Participant` that never registered an encryption key for the session (e.g., an index outside the commitment set, or a participant that failed to submit commitments), the indexing panics, crashing the processor — a denial of service. This matches the Medium-severity, availability-only impact of the original advisory.

### Likelihood Explanation
Triggering requires two conditions on the message path:

1. `msg.pop.verify(...)` must pass, so `share` must be a genuinely valid `EncryptedMessage` for `from` — but such messages are protocol-public (they are the DKG secret-share ciphertexts), so an accuser can replay a real one.
2. `proof` must be `Some`, i.e., the supplied `blame` bytes must parse as an `EncryptionKeyProof` — `EncryptionKeyProof::read` only requires well-formed `read_G`/`DLEqProof::read` bytes, which anyone can produce.

The precondition is then that `decryptor` (the accuser index) is absent from `enc_keys`. `enc_keys` is keyed strictly by registered commitment messages, not by `1..=n`, so any valid `Participant` index that didn't submit a commitment message for that session satisfies it. This is a moderately narrow but plausible state in a DKG with faulty/absent participants — hence Medium likelihood rather than High.

*Caveat:* I could not fully trace `AdditionalBlameMachine::blame` to confirm whether `accuser` is range-checked against registered participants before `decrypt_with_proof` is invoked; if an earlier layer rejects unregistered accusers, reachability drops and this is not exploitable. The indexing itself (`self.enc_keys[&decryptor]`, encryption.rs:388) is confirmed unguarded.

### Recommendation
Replace the panicking index with a fallible lookup:

```rust
let Some(decryptor_key) = self.enc_keys.get(&decryptor) else {
  Err(DecryptionError::InvalidProof)?
};
// use *decryptor_key in the DLEq bases
```

and add `decryptor`/`from` presence validation in `Decryption::register` consumers (`AdditionalBlameMachine::blame`) so blame proofs naming unregistered participants are rejected as `InvalidProof` instead of panicking.

### Proof of Concept
```rust
// Construct a Decryption with no registered keys (or keys only for some participants)
let mut dec = Decryption::<Ristretto>::new(context);
// Register participant 1 only
dec.register(Participant::new(1).unwrap(), msg_for_1);

// Attacker supplies, via blame verification:
//   from = accused participant with a real EncryptedMessage (pop verifies)
//   decryptor = Participant::new(2) — never registered
//   proof = Some(any well-formed EncryptionKeyProof)
// Panics at encryption.rs:388: self.enc_keys[&decryptor]
let _ = dec.decrypt_with_proof::<SecretShare< F >>(
  Participant::new(1).unwrap(), // from (valid signed msg)
  Participant::new(2).unwrap(), // decryptor absent from enc_keys
  real_encrypted_message_from_1,
  Some(attacker_crafted_proof),
);
// Result: thread panic (index into HashMap with missing key) => DoS
```