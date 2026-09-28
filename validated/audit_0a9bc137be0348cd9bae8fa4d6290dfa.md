The strong analog candidate: PedPoP encryption keys / commitments accepting identity points. Let me check the encryption module.### Title
PedPoP accepts `enc_key = identity`, making every secret share encrypted to that participant publicly decryptable - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
`EncryptionKeyMessage::read` deserializes a participant's ECDH encryption key with `C::read_G`, which enforces only a canonical point encoding — it does not reject the identity element (unlike `Curve::read_G` in `crypto/frost/src/curve/mod.rs`, which explicitly rejects identity). A participant who registers the identity point as their encryption key causes every secret share addressed to them to be encrypted under the publicly-known ECDH shared point `ephemeral * identity = identity`. Any observer of the wire (e.g., the coordinator/tributary relaying `DkgShares` bytes) can derive the ChaCha20 key and recover all shares sent to that participant — the exact analog of `buyShares(to = address(0))`: an unvalidated "zero" destination to which sensitive material is delivered.

### Finding Description
- `EncryptionKeyMessage::read` reads `enc_key` with `C::read_G(reader)` and performs no identity check (encryption.rs:57-58).
- `Ciphersuite::read_G` only checks canonicality; identity is a valid canonical encoding (crypto/ciphersuite/src/lib.rs:91-101).
- `verify_r1` registers each peer's `enc_key` via `self.encryption.register(l, msg)` and validates only the commitments count and the PoK on `commitments[0]` — the encryption key itself is never checked (lib.rs:313-331).
- `Encryption::encrypt` computes `ecdh(&key, to)` where `to` is the registered `enc_key`; with `to = identity` the shared point is the identity regardless of the ephemeral private key (encryption.rs:95-97, 154).
- `cipher` derives the ChaCha20 key solely from `context` and the shared point's encoding — for `enc_key = identity` this is a deterministic, publicly computable value (encryption.rs:101-133).
- The decryptor-side `ecdh(&self.enc_key, msg.key)` equals the same point (`priv * (ephemeral * G) = ephemeral * pub`), so decryption succeeds normally and `calculate_share` raises no error; the malformed registration is completely silent (encryption.rs:487-488, lib.rs:476-490).

Note that the `Commitments` PoK binds `commitments[0]`, not `enc_key`, so it provides no protection here, and the per-message Schnorr PoP in `EncryptedMessage` only proves the sender knew the *ephemeral* scalar — it does not authenticate the recipient key either.

### Impact Explanation
Every `SecretShare` encrypted to the rogue participant is confidential-in-name-only. Because shares are routed through public coordinator/tributary transactions (see `Transaction::DkgShares` handling in `processor/src/key_gen.rs` and `coordinator` transaction decoding), a passive observer can compute `cipher(context, identity)` and plaintext-decrypt all shares addressed to that participant, then sum them to recover that participant's full interpolated secret share — a direct key-share recovery by an unprivileged party. The DKG's threshold secrecy is reduced by one share per identity-key registrant with no fault or blame generated, and the attack is repeatable across sessions since nothing binds or validates `enc_key`.

### Likelihood Explanation
An unprivileged DKG participant only needs to publish a well-formed `EncryptionKeyMessage` whose final bytes encode the identity point (e.g., the compressed `0x00...01` / identity encoding for the curve); all PoK checks still pass because they cover `commitments[0]`, not `enc_key`. No collusion, timing, or privileged access is required, and ciphertexts are observable on the share-distribution channel.

### Recommendation
Reject the identity element when reading the encryption key: in `EncryptionKeyMessage::read` (and in `Decryption::register` / `Encryption::register` as defense in depth), check `enc_key.is_identity()` and return an error / `PedPoPError::InvalidCommitments`. More broadly, require a proof of possession for `enc_key` (a Schnorr signature over the key bound to `(context, participant)`), which simultaneously prevents identity, key-co-option, and key-reuse attacks.

### Proof of Concept
1. Malicious participant `m` runs `KeyGenMachine::generate_coefficients` normally but constructs the broadcast `EncryptionKeyMessage<Commitments>` bytes manually: valid `t` commitments + valid PoK signature, followed by the canonical identity encoding as `enc_key`.
2. Every other participant's `verify_r1` accepts it (no identity check), and `generate_secret_shares` emits `EncryptedMessage`s to `m` encrypted with shared point `identity`.
3. An observer reading the `DkgShares` payloads computes `key = cipher(context, identity_point_encoding)` independently — needing no private key, since `ephemeral_priv * identity = identity` for any ephemeral — and applies the ChaCha20 keystream to each ciphertext `msg` field, recovering each sender's `SecretShare` to `m`.
4. Summing the decrypted shares over all senders yields participant `m`'s final `secret_share` (matching `C::generator() * share == verification_shares[m]`), constituting key-share recovery with zero detected faults.