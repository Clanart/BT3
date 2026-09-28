### Title
Unreachable-registration indexing panic on untrusted DKG message flow causes denial of service - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`Encryption::encrypt` and `Decryption::decrypt_with_proof` index the `enc_keys` HashMap with `self.enc_keys[&participant]` / `self.enc_keys[&decryptor]` (encryption.rs:388, encryption.rs:466). `HashMap`'s `Index` impl panics when the key is absent. The map is only populated by `Decryption::register`, which consumes a per-participant `EncryptionKeyMessage` (encryption.rs:351-362) — data supplied by remote DKG participants. Any path that attempts to encrypt to, or verify a blame proof naming, a `Participant` whose registration message was never received (withheld, malformed, or parsed with different params) hits an unconditional panic instead of an `Err`, aborting the signing process. This mirrors CVE-2020-19481's class: an invalid read (here, an unchecked map index) on attacker-influenced state reachable via crafted protocol input, yielding DoS.

### Finding Description
- `Decryption::register` inserts into `enc_keys` keyed by `Participant` and only asserts against *re*-registration; it cannot guarantee every expected participant is present.
- `Encryption::encrypt` at `crypto/dkg/pedpop/src/encryption.rs:466` does `self.decryption.enc_keys[&participant]` — panics if `participant` was never registered.
- `Decryption::decrypt_with_proof` at `crypto/dkg/pedpop/src/encryption.rs:388` does `self.enc_keys[&decryptor]` inside the blame/decryption path — panics if the named decryptor was never registered.
- Both are reachable while processing messages whose contents and senders are controlled by unthreshold peers: `EncryptionKeyMessage::read` (encryption.rs:57) and `EncryptedMessage::read` (encryption.rs:171) deserialize `C::read_G`, `SchnorrSignature::read`, and per-participant message fields from untrusted bytes; a faulty participant can simply omit its registration or desynchronize the registration set, then trigger encrypt/decrypt_with_proof.
- The adjacent `assert!(!enc_keys.contains_key(&participant))` at encryption.rs:356-358 is likewise a panic on duplicated registration; while PedPoP's docs push duplicate-commitment detection to the caller, the *missing*-key index panics at 388/466 are not documented as caller obligations and cannot be converted to a graceful `Err` by the library user without forking.

### Impact Explanation
A single faulty or malicious DKG participant can panic every honest party's process during the share-encryption or blame-verification phase, permanently halting key generation/signing (availability loss). The panic occurs after `io::Error`-returning deserialization succeeded, so it is not caught by `read` error handling; in Rust this unwinds or aborts the caller's thread. Severity Medium: availability-only, no secret disclosure, matching the CVE's A:H / C:N / I:N profile.

### Likelihood Explanation
Reachable by any unprivileged party able to participate in (or feed messages into) a DKG session: withholding or corrupting an `EncryptionKeyMessage`, or triggering `decrypt_with_proof` against a decryptor absent from `enc_keys`, suffices. No collusion or threshold of malicious actors is required.

### Recommendation
Replace the indexing `HashMap` accesses at `crypto/dkg/pedpop/src/encryption.rs:388` and `:466` with `.get(&participant).ok_or(...)` / `.get(&decryptor).ok_or(DecryptionError::...)` returning typed errors, and change `Decryption::register`'s `assert!` to a checked `Result` so duplicate/missing registrations surface as `FrostError`/IO errors instead of aborts.