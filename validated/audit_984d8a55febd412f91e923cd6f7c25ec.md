### Title
Duplicate PedPoP encryption-key registration causes a panic (assertion failure) in `Decryption::register` - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
JLSEC-2026-538 describes an out-of-bounds read in openjpeg reachable by crafted input, whose impact is application availability. In Rust, the analogous bug class is an unhandled panic on attacker-controlled input reaching a decryption/parsing path. `Decryption::register` in PedPoP asserts that a participant's encryption key has not already been registered; a DKG participant who submits a second `EncryptionKeyMessage` under the same `Participant` index triggers the assertion and panics the host process.

### Finding Description
`Encryption::register` delegates to `Decryption::register`, which contains:

```rust
// crypto/dkg/pedpop/src/encryption.rs:356-358
assert!(
  !self.enc_keys.contains_key(&participant),
  "Re-registering encryption key for a participant"
);
self.enc_keys.insert(participant, msg.enc_key);
```

`participant` and the message bytes both come from an untrusted `EncryptionKeyMessage` read via `EncryptionKeyMessage::read` (`C::read_G`, `M::read` on attacker bytes) at `crypto/dkg/pedpop/src/encryption.rs:57-59`. Nothing in the read path or in `register` deduplicates per-participant submissions; the code assumes the caller enforced a one-registration-per-participant invariant. If the PedPoP driver (which feeds each participant's encryption-key message into `Encryption::register`/`Decryption::register`) is invoked once per received message — as it is in Serai's DKG flow — any unprivileged participant can send their `EncryptionKeyMessage` twice (e.g., in the same round set, or replayed across a retry/rebroadcast of the keygen step) and hit the `assert!`.

A related reachable panic exists at `crypto/dkg/pedpop/src/encryption.rs:466` (`self.decryption.enc_keys[&participant]`) and `:388` (`self.enc_keys[&decryptor]`), which index the map directly and panic on a missing key — e.g., `decrypt_with_proof` called for a `decryptor` that never registered, triggered during blame-proof processing of attacker-submitted `EncryptionKeyProof` data.

### Impact Explanation
Availability loss, matching the CVE's rated impact (A:H, no C/I). A single unprivileged DKG participant can abort the key-generation session (and any task sharing the panic, depending on executor isolation) by replaying their encryption-key registration, repeatedly stalling threshold-key ceremonies. No secret material is leaked and no invalid key is produced — it is a pure denial of service, consistent with Medium severity.

### Likelihood Explanation
The crash requires only that a valid participant index be presented to `register` twice. Participant indexes are public, non-secret u16 values, and PedPoP messages are exchanged on an authenticated-but-not-duplication-checked channel; a participant can legitimately cause their message to be delivered more than once (retry, reconnect, or simply sending it in two batches). No cryptographic work or collusion is needed — a single honest-but-buggy or mildly malicious validator suffices.

### Recommendation
Replace the `assert!` in `Decryption::register` with an error return (e.g., `Result<M, Error>` with a `DuplicateRegistration` variant) or an idempotent check that verifies the re-registered `enc_key` is identical to the stored one, so a repeated message is ignored rather than fatal. Similarly, replace `self.enc_keys[&participant]`/`self.enc_keys[&decryptor]` indexing with `.get()` plus an error path, since missing registration is attacker-derivable state, not a local invariant.

### Proof of Concept
```rust
// Given an Encryption<C> built for our participant index `i`,
// and an EncryptionKeyMessage `msg` for some other participant `p`:
let mut enc = Encryption::<C>::new(context, i, &mut rng);

// Attacker (participant p) sends their EncryptionKeyMessage twice:
let m1 = EncryptionKeyMessage::<C, M>::read(&mut bytes.as_slice(), params).unwrap();
enc.register(p, m1.clone()); // succeeds, inserts enc_keys[p]

let m2 = EncryptionKeyMessage::<C, M>::read(&mut bytes.as_slice(), params).unwrap();
enc.register(p, m2); // assert! fires: "Re-registering encryption key for a participant"
```

Same outcome via the indexing path: call `decrypt_with_proof` with a `decryptor` index that never completed registration — `self.enc_keys[&decryptor]` panics on the missing key. Both are reachable purely from bytes the attacker supplies to `EncryptionKeyMessage::read` / `EncryptionKeyProof::read` and the public `Participant` index, requiring no secret or collusion.

Caveat: whether upstream PedPoP driver code filters duplicate per-participant messages before calling `register` could not be fully confirmed within the available scope; if such filtering exists and is enforced on all entry paths, the reachable surface narrows to the `enc_keys[...]` indexing panics during blame/decryption handling.