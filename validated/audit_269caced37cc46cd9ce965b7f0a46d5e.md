### Title
Attacker-triggerable panic on duplicate/missing encryption-key registration in PedPoP DKG - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The referenced upstream fix (zksync-era #1822) converts panic paths into proper errors for edge cases a remote party can influence. The same bug class exists in Serai's PedPoP DKG: `Decryption::register` uses `assert!` to reject a second `EncryptionKeyMessage` for the same `Participant`, and `decrypt_with_proof` indexes `self.enc_keys[&decryptor]` with `HashMap`'s panicking `Index` impl when the named decryptor was never registered. Both are reachable from attacker-controlled DKG messages (`EncryptionKeyMessage::read` consumes untrusted bytes) and abort the process instead of returning an error.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs:351-362`, `Decryption::register` panics if `participant` is already present in `enc_keys`:

```rust
assert!(
  !self.enc_keys.contains_key(&participant),
  "Re-registering encryption key for a participant"
);
```

`EncryptionKeyMessage::read` (encryption.rs:57-59) parses `msg` and `enc_key` straight from the wire. Each participant can broadcast an `EncryptionKeyMessage`; nothing inside `register` dedups gracefully — a malicious (or simply buggy/reconnecting) participant that delivers two registration messages causes an unconditional panic in every honest node that processes both.

Similarly, `decrypt_with_proof` (encryption.rs:388) evaluates `self.enc_keys[&decryptor]` inside `proof.dleq.verify(...)`. `std::collections::HashMap`'s `Index` implementation panics on absent keys, so a blame/accusation flow that names a `decryptor` whose encryption key was never registered panics rather than returning `DecryptionError`. `Encryption::encrypt` (encryption.rs:466) has the same pattern: `self.decryption.enc_keys[&participant]` panics when a share is being encrypted to a participant that never registered.

Note the contrast with the rest of the crate: `Commitments::read` (pedpop/lib.rs:110-128), `EncryptedMessage::read`, `read_G`/`read_F` (ciphersuite/lib.rs:74-101) all return `io::Result` on malformed input. The registration/decrypt lookups are the inconsistent panic paths, exactly mirroring the upstream bug class.

### Impact Explanation
An unprivileged DKG participant (or a relay/peer able to inject a duplicate registration message before honest processing completes) crashes every honest party running PedPoP. In a coordinator/validator deployment this halts key generation / resharing. For `decrypt_with_proof`, a malicious accuser can crash a node during blame resolution by referencing an unregistered decryptor. This is a consensus-grade liveness failure (all nodes panic on the same input) — Medium severity DoS consistent with the upstream "panics caused an outage" report.

### Likelihood Explanation
Triggering `register`'s assert only requires sending two `EncryptionKeyMessage`s from the same `Participant` index — trivial for a faulty participant or a network-layer duplicate. The `enc_keys[&decryptor]` path requires an accusation naming a decryptor who never registered (e.g., a participant excluded/removed before registration, or an index the sender fabricates). `Participant` deserialization already rejects index 0 and out-of-range values, so the input is cheap to craft.

### Recommendation
Replace the `assert!` in `Decryption::register` with a fallible check returning an error (e.g., extend `DecryptionError` or return `io::Error`), matching the crate's error-based handling elsewhere. In `decrypt_with_proof` and `Encryption::encrypt`, replace `enc_keys[&participant]` indexing with `.get(&participant).ok_or(...)`/`?` so unknown or unregistered participants produce `DecryptionError::InvalidProof` (or a new variant) instead of a panic.

### Proof of Concept
```rust
// crypto/dkg/pedpop: with any ThresholdParams and Encryption<C>::new(...)
let mut enc = Encryption::<Ristretto>::new(context, Participant(1), &mut OsRng);

let msg = EncryptionKeyMessage::<Ristretto, M>::read(
    &mut attacker_bytes.as_ref(), params,
).unwrap();

// Honest flow registers the attacker's key once:
enc.register(Participant(2), msg.clone());
// Attacker re-sends (duplicate gossip / reconnect / deliberate):
enc.register(Participant(2), msg); // PANIC: "Re-registering encryption key..."

// Or, during blame resolution:
// decrypt_with_proof(from, decryptor=Participant(9), msg, Some(proof))
// where Participant(9) never registered -> HashMap index panic at enc_keys[&decryptor].
```

Both panics are reachable purely from bytes an unprivileged participant supplies via `EncryptionKeyMessage::read` / the accusation flow, with no malformed-encoding requirements — the panic is a logic-level duplicate/missing-key condition, identical in spirit to the upstream "config not ready → panic" outage class.