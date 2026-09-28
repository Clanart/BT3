### Title
PedPoP `EncryptionKeyMessage` accepts an identity (or low-order-derived) encryption key, causing secret shares to be encrypted to a publicly known ECDH point - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The external report's bug class — accepting attacker-controlled input without resolving/validating what it actually points to, thereby leaking secret bytes — maps directly onto PedPoP's per-participant encryption key registration. `EncryptionKeyMessage::read` parses the peer's `enc_key` with `Ciphersuite::read_G`, which enforces canonicality but does **not** reject the identity point. An unprivileged participant can therefore register the identity point as their encryption key. Every honest party then ECDH's their per-message ephemeral scalar against the identity, producing `ecdh = k * O = O` — a publicly known shared "secret". The ChaCha20 cipher key becomes deterministically derivable by anyone, so the ciphertext carrying a DKG `SecretShare` to that participant is decryptable by any observer, not just the intended recipient.

### Finding Description
`EncryptionKeyMessage::read` reads `enc_key` via `C::read_G`, which resolves to `Ciphersuite::read_G` since `Encryption<C>` / `Decryption<C>` are bounded on `Ciphersuite`, not `Curve` — so the identity-rejection in `Curve::read_G` (crypto/frost/src/curve/mod.rs:125-131) does not apply. `Ciphersuite::read_G` only rejects non-canonical/invalid encodings (crypto/ciphersuite/src/lib.rs:91-100). `Decryption::register` stores the key with no further validation (encryption.rs:351-362). On send, `encrypt` computes `cipher(context, &ecdh(key, to))` where `ecdh = private * public` (encryption.rs:95-97, 153-154); with `public = identity`, `ecdh = identity` for every message and every sender. `cipher` derives the ChaCha20 key solely from `context || ecdh_bytes` (encryption.rs:101-133), so anyone can recompute it.

### Impact Explanation
All `EncryptedMessage<C, SecretShare<C::F>>` values addressed to the malicious participant are encrypted under a world-computable keystream. Since DkgShares transactions are broadcast/observable, any passive observer decrypts every share destined for index `l` and sums them to recover participant `l`'s full threshold secret share — a valid signing share of the group key — without compromising any validator. This is key-share recovery reachable purely from public, attacker-supplied message bytes.

### Likelihood Explanation
The attacker only needs to submit a syntactically valid DKG commitments message with `enc_key = identity`. No PoK binds `enc_key` (the `sig` covers only the coefficients), nothing rejects the registration, and `register`'s only check is duplicate-participant. Reaching it requires participating in a DKG attempt, which an unprivileged party slated as a participant can do; the leakage also exposes the share to third parties who are not participants at all, which the attacker could not otherwise achieve (they'd only hold their own share).

### Recommendation
In `EncryptionKeyMessage::read` (or `Decryption::register`), reject `enc_key` for which `is_identity()` holds, analogous to `Curve::read_G`. Additionally consider requiring a proof of possession over `enc_key` bound into the commitment transcript.

### Proof of Concept
1. Attacker (participant `l`) serializes an `EncryptionKeyMessage` with `enc_key` set to the 32-byte canonical identity encoding (e.g., `0x01 || 0x00*31` for Ed25519/Ristretto, identity compressed form).
2. All parties `i` call `encryption.encrypt(rng, l, share_i_l)`; each computes `ecdh = share_key_i * identity = identity`.
3. ChaCha20 key = first 32 bytes of `transcript("DKG Encryption v0.2", context, domain "encryption_key", shared_key = identity_encoding).challenge("key")` with IV `"DKG IV v0.2\0"` — computable by anyone knowing `context`.
4. Observer reads each broadcast `EncryptedMessage`, applies the keystream, obtains `SecretShare` bytes for every `i → l`, parses each `C::F`, sums them → `l`'s complete secret share.

Relevant code: `EncryptionKeyMessage::read` (crypto/dkg/pedpop/src/encryption.rs:57-59), `ecdh`/`cipher`/`encrypt` (encryption.rs:95-167), permissive `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-100) vs. identity-rejecting `Curve::read_G` (crypto/frost/src/curve/mod.rs:125-131).