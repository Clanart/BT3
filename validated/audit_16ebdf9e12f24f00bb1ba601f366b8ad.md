### Title
Panic on duplicated participant registration / unregistered decryptor lookup crashes DKG participants - ([File: crypto/dkg/pedpop/src/encryption.rs])

### Summary
CVE-2023-1992 is a Wireshark dissector crash caused by feeding crafted bytes to a parser (denial of service on untrusted input). The closest analog in Serai's in-scope code is a panic reachable from untrusted protocol messages in the PedPoP encryption layer: `Decryption::register` asserts that a participant's encryption key has not already been registered, and `decrypt_with_proof` indexes `self.enc_keys[&decryptor]` unconditionally. Both are reachable by bytes a counterparty sends (`EncryptionKeyMessage::read`, `EncryptionKeyProof::read`), turning a malformed/duplicated message into a process crash rather than an `io::Error`.

### Finding Description
`EncryptionKeyMessage::read` and `EncryptedMessage::read` accept attacker-controlled bytes (`C::read_G`, `SchnorrSignature::read`) and are handed to `Encryption::register`, which forwards to `Decryption::register`. That function contains:

```rust
assert!(
  !self.enc_keys.contains_key(&participant),
  "Re-registering encryption key for a participant"
);
```
(crypto/dkg/pedpop/src/encryption.rs:356-359)

The surrounding `read`/`write` machinery is careful to return `io::Result` for every other malformed-input condition, but this one invariant is enforced with `assert!`. A participant who causes their `EncryptionKeyMessage` to be processed twice — e.g., by sending a duplicate registration message for the same `Participant` index, or because the DKG session replays/re-broadcasts a message set containing their index twice — panics the honest party processing the batch.

Additionally, in `decrypt_with_proof`, the blame/decryption path does `self.enc_keys[&decryptor]` (encryption.rs:388). If an accusation flow is invoked naming a `decryptor` participant whose encryption key was never registered (which is itself an attacker-influenced condition, since registration depends on messages other participants choose to send), the `HashMap` index panics.

### Impact Explanation
A crash in the DKG/key-generation path aborts the threshold-key ceremony. In Serai's deployment this runs on processors/validators participating in PedPoP; a single malicious or buggy participant can repeatedly crash honest nodes each time the key-generation round is attempted, preventing multisig formation. This is a network-reachable, unprivileged denial of service — the attacker only needs to send well-formed-typed but semantically invalid messages (`EncryptionKeyMessage`, `EncryptionKeyProof`), matching the "crafted capture/packet → crash" shape of the CVE. Per the rules this is a Medium-severity DoS: it halts availability without key compromise, and it is not a "documented MUST misuse" because the function gives no indication that duplicate registration is fatal rather than an error.

### Likelihood Explanation
Likelihood is moderate: it requires a malicious participant in a DKG session (or a misbehaving relay that replays messages), but no cryptographic computation — just sending a duplicate `EncryptionKeyMessage` for the same `Participant`. The panic path is deterministic once triggered (`HashMap::contains_key` check then `assert!`), and the second path (`enc_keys[&decryptor]`) triggers whenever blame resolution references a participant that skipped registration.

### Recommendation
Return errors instead of panicking. `Decryption::register` should return a `Result`/`DkgError`-style variant (e.g., `DuplicateEncryptionKey(participant)`) that callers can convert into `FrostError`/`io::Error`, and `decrypt_with_proof` should use `enc_keys.get(&decryptor).ok_or(DecryptionError::InvalidProof)` rather than indexing. Any invariant enforced on deserialized counterparty data must propagate as an error, consistent with the crate's `io::Result` conventions.

### Proof of Concept
```rust
// Within crypto/dkg/pedpop, given an Encryption<C> instance `enc` for
// participant i, and a serialized EncryptionKeyMessage from participant p:
let msg_bytes: Vec<u8> = /* attacker-supplied bytes, validly encoded */;
let mut r1 = msg_bytes.as_slice();
let m1 = EncryptionKeyMessage::<C, M>::read(&mut r1, params).unwrap();
enc.register(Participant::new(p).unwrap(), m1);

// Attacker sends the same registration again (replay or duplicate submission)
let mut r2 = msg_bytes.as_slice();
let m2 = EncryptionKeyMessage::<C, M>::read(&mut r2, params).unwrap();
enc.register(Participant::new(p).unwrap(), m2); // panic: "Re-registering encryption key"

// Second path: call decryption blame logic naming a decryptor that never
// registered an enc_key:
let dec: Decryption<C> = /* fresh, enc_keys empty */;
let proof = EncryptionKeyProof::<C>::read(&mut proof_bytes.as_slice()).unwrap();
let emsg = EncryptedMessage::<C, E>::read(&mut msg_bytes.as_slice(), params).unwrap();
// After the pop signature verifies, this line panics:
//   self.enc_keys[&decryptor]  -- key not present
dec.decrypt_with_proof(from, unregistered_decryptor, emsg, Some(proof));
```

Note on scope confidence: in-scope deserialization helpers (`C::read_F`, `C::read_G`, `SchnorrSignature::read`, `DLEqProof::read`, `ThresholdKeys::read`, `Commitments::read`, `SignatureShare::read`) all correctly propagate malformed bytes as `io::Error`, and `ThresholdKeys::read` bounds its allocations by `u16` fields, so the raw parsers themselves are not the crash site — the reachable panics are the PedPoP `assert!` and `HashMap` indexing shown above.