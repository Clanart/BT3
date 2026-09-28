### Title
Identity encryption key accepted in PedPoP DKG causes all secret shares sent to that participant to be encrypted under a publicly-known key - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptionKeyMessage::read` parses the participant's ECDH encryption key with `Ciphersuite::read_G`, which validates canonical encoding but does not reject the identity point. A DKG participant who registers `enc_key = identity` forces every `encrypt()` call targeting them to derive the ChaCha20 key from a constant, publicly-computable ECDH result (`identity * r = identity`). Since DKG shares are broadcast as public bytes (e.g., the `DkgShares` tributary transaction), anyone can decrypt all shares addressed to that participant and recover their threshold secret share.

### Finding Description
In `crypto/dkg/pedpop/src/encryption.rs:57-59`, `EncryptionKeyMessage::read` reads `enc_key` via `C::read_G(reader)`, which resolves to `Ciphersuite::read_G` in `crypto/ciphersuite/src/lib.rs:91-101`. That implementation only checks decompression success and canonical re-encoding; the identity point is a valid canonical encoding and is accepted. Contrast with `Curve::read_G` in `crypto/frost/src/curve/mod.rs:125-131`, which explicitly rejects identity — PedPoP's encryption registration does not use that stricter reader.

`Decryption::register` (`encryption.rs:351-362`) stores whatever `enc_key` was supplied with no validation. Later, `Encryption::encrypt` → `encrypt` (`encryption.rs:135-167`) computes the shared key as `ecdh(key, to)` where `to` is the registered `enc_key`. With `to = identity`, `ecdh` returns `identity` for any ephemeral `key`, so `cipher()` (`encryption.rs:101-133`) derives a ChaCha20 key purely from the transcript of the identity point's encoding and the public `context` — bytes anyone can reproduce. The per-message proof-of-possession does not help: it only proves knowledge of the per-message key's discrete log, which the honest sender genuinely has; it never binds to the recipient's registered key.

On the receiving side, `KeyMachine::calculate_share` (`crypto/dkg/pedpop/src/lib.rs:463-499`) decrypts normally and passes blame checks, so the attack produces no errors and no blame — the attacker is not even flagged faulty.

### Impact Explanation
Every `SecretShare` ciphertext addressed to the malicious participant is decryptable by any observer who sees the broadcast bytes (in Serai, shares are posted on-chain in `Transaction::DkgShares`). Summing the decrypted `f_i(l)` values yields that participant's complete threshold secret share — a key-share recovery primitive reachable purely from public inputs the attacker supplies in their commitments message. This silently lowers the effective security of the resulting `ThresholdKeys`: a coalition holding `t - 1` other shares can combine them with the publicly recoverable share to reconstruct the group private key, defeating the `t`-of-`n` threshold without triggering any abort or blame.

### Likelihood Explanation
An unprivileged DKG participant can trigger this by submitting a `DkgCommitments` message whose trailing `enc_key` field is the canonical 32-byte encoding of the identity point (e.g., all-zero/identity encoding for Ristretto/Ed25519). No PoK, signature, or interactive cooperation covers the encryption key, so the attack requires no additional capability beyond participating in a DKG session and later having accomplices or observers read the on-chain shares. It is fully deterministic and undetectable by the protocol's verification.

### Recommendation
Reject the identity point when reading the encryption key in `EncryptionKeyMessage::read` (use a `read_G` variant that errors on `is_identity`, as `Curve::read_G` does), and/or assert `!enc_key.is_identity()` in `Decryption::register`. Additionally consider including the recipient's registered `enc_key` in the `pop_challenge` transcript or share-ciphertext binding so key substitution/weak-key registration is caught.

### Proof of Concept
1. Malicious participant `l` builds a `Commitments` message normally, but sets the appended `enc_key` field in `EncryptionKeyMessage::serialize()` to `<C::G as Group>::identity().to_bytes()` (canonical, passes `read_G`).
2. All honest participants call `register(l, msg)`, storing `identity` as `l`'s encryption key.
3. Each honest sender's `encrypt(rng, context, l, share)` computes `ecdh(key, identity) = identity` and encrypts the share with `ChaCha20::new(H(transcript(context, identity_bytes))[..32], static_iv)`.
4. An observer recomputes the identical cipher from the public `context` and the known identity encoding, applies the keystream to each ciphertext byte blob sent to `l`, obtains every `SecretShare` scalar `f_i(l)`, and sums them to recover `l`'s secret share — verified against `verification_shares[l]` via `C::generator() * share == verification_shares[l]`.