### Title
PedPoP accepts identity ECDH keys, making encrypted DKG secret shares publicly decryptable - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
Analogous to a `DOMAIN_SEPARATOR` being finalized before the identifying parameter (`name`) is bound, PedPoP's share-encryption cipher derives its ChaCha20 keystream from only the public `context` and the ECDH point, using a fixed IV — while the ECDH peer keys (`enc_key` in `EncryptionKeyMessage` and the ephemeral `key` in `EncryptedMessage`) are decoded with `Ciphersuite::read_G`, which does not reject the identity point. A participant who registers `enc_key = identity` forces every share "encrypted" to them to use `ecdh = identity`, producing a keystream any observer who knows the public `context` can recompute — exposing threshold secret shares to unprivileged third parties.

### Finding Description
`EncryptionKeyMessage::read` deserializes `enc_key` via `C::read_G` where `C: Ciphersuite` (crypto/dkg/pedpop/src/encryption.rs:57-58), and `EncryptedMessage::read` does the same for the ephemeral `key` (encryption.rs:171-176). The `Ciphersuite` bound is the plain group-decoding function — the FROST crate's own `Curve::read_G` exists precisely because the base `Ciphersuite::read_G` does not reject identity, adding that check on top (crypto/frost/src/curve/mod.rs:123-131). PedPoP never applies that check.

`Decryption::register` stores the peer-supplied `enc_key` per participant without any validity check beyond non-re-registration (encryption.rs:351-362). `Encryption::encrypt` then computes `ecdh(key, to)` where `to` is that stored `enc_key` (encryption.rs:466), and the cipher key is `transcript("DKG Encryption v0.2", context, shared_key)` with a hard-coded IV `b"DKG IV v0.2\0"` (encryption.rs:101-133). If `enc_key` is the identity, `ecdh = identity * k = identity`, so the keystream is fully determined by `context` — a caller-supplied `[u8; 32]` documented only as needing uniqueness, not secrecy (crypto/dkg/pedpop/src/lib.rs:147-148). The same applies to a malicious or malformed ephemeral `msg.key` on the decrypt path (encryption.rs:487-488).

### Impact Explanation
Every `SecretShare` a sender produces for the malicious participant is wrapped by `encrypt` into `EncryptedMessage` and sent over the assumed-authenticated channel (lib.rs:366-369). Anyone observing that ciphertext who knows `context` (e.g., derivable from public session/set parameters) recomputes `cipher(context, identity)` and recovers the plaintext share — a threshold key share leaked to a party who was never a DKG participant. This defeats the entire purpose of the ECDH+ChaCha20 layer and the per-message PoP. Once `t` shares leak this way (one per corrupted recipient registration suffices to leak that recipient's share slot), an observer can contribute toward group secret recovery. This is key-share recovery reachable purely from bytes fed to `EncryptionKeyMessage::read` / `EncryptedMessage::read`.

### Likelihood Explanation
An unprivileged DKG participant controls the `enc_key` they broadcast in their round-1 `EncryptionKeyMessage` and the `key`/`pop` fields of any `EncryptedMessage` they produce. Nothing in `Commitments`, the PoK (`challenge` binds only context/participant/nonce/commitments, lib.rs:86-94), or `register` validates that `enc_key` is non-identity. The attack requires no collusion and no privileged position — only submitting a crafted commitment message and passive observation of the (authenticated but not necessarily confidential) channel carrying ciphertexts. Exploitation is deterministic; no probabilistic or timing element exists.

### Recommendation
- Reject identity points in `EncryptionKeyMessage::read` and `EncryptedMessage::read` (use an identity-rejecting `read_G` equivalent to `Curve::read_G`, or explicitly check `is_identity` after decode), and/or assert `!enc_key.is_identity()` in `Decryption::register` and `!msg.key.is_identity()` before `ecdh` in `decrypt`.
- Additionally bind the recipient identifier (and ideally the per-message ephemeral `key`) inside `cipher()`'s transcript so the keystream is domain-separated per (sender, recipient, message) rather than solely by context + ECDH point.

### Proof of Concept
```rust
// Malicious participant `l` broadcasts an EncryptionKeyMessage whose
// enc_key is the canonical encoding of the identity point.
let mut msg = Commitments { commitments, cached_msg, sig }.serialize();
msg.extend(<C::G as GroupEncoding>::to_bytes(&C::G::identity()).as_ref());
// -> EncryptionKeyMessage::read succeeds; register() stores identity.

// Honest sender computes shares and calls:
//   encrypt(rng, context, i, enc_keys[&l], share_bytes)
// ecdh = identity * k = identity
// keystream = ChaCha20(key = H("DKG Encryption v0.2" || context || identity_bytes),
//                      iv  = "DKG IV v0.2\0")

// Any observer knowing `context` (public session identifier) reconstructs the
// same keystream and XORs it against EncryptedMessage.msg to recover the
// scalar share intended for `l` — no ECDH secret needed.
```
Root cause verified at crypto/dkg/pedpop/src/encryption.rs:57-58 (`enc_key` read without identity check), :95-97 (`ecdh` on unchecked point), :101-133 (cipher keyed only by public context + ECDH + static IV), and :351-362, :466 (unvalidated key stored and used); the missing identity check is confirmed by contrast with crypto/frost/src/curve/mod.rs:123-131, which adds the rejection `Ciphersuite::read_G` lacks.