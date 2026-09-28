### Title
PedPoP accepts an identity encryption key, producing a publicly-derivable ECDH shared secret for DKG shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptionKeyMessage::read` and `EncryptedMessage::read` deserialize the per-participant encryption key and the per-message ephemeral key using `Ciphersuite::read_G`, which validates canonically but — unlike `Curve::read_G` in FROST — does **not** reject the identity point. The identity key then flows into `ecdh()`, which multiplies the local secret scalar `enc_key` by the attacker-supplied point. With an identity input the ECDH output is the identity point, a value known to everyone, so the ChaCha20 cipher key derived from it via `cipher()` is public. This mirrors the bug class of the Geode report: crafted, untrusted input reaching a privileged operation (here, scalar multiplication by a secret) in a way that voids the access protection it was supposed to provide.

### Finding Description
- `Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-100`) only checks decoding success and canonical re-encoding. Identity passes.
- FROST explicitly wraps this with an identity rejection (`crypto/frost/src/curve/mod.rs:125-131`), showing the codebase knows `read_G` alone is insufficient where identity is dangerous.
- `EncryptionKeyMessage::read` (`crypto/dkg/pedpop/src/encryption.rs:57-59`) reads `enc_key` with `C::read_G` with no identity check and — critically — **no proof of possession or proof of knowledge of the corresponding scalar**. The key is accepted verbatim into `Decryption::register` (`encryption.rs:352-362`), which only asserts the participant hasn't registered before.
- `EncryptedMessage::read` (`encryption.rs:171-177`) likewise reads `key` with `C::read_G`, allowing `key = identity`.
- `ecdh()` (`encryption.rs:95-97`) computes `public * private` with no contributory-behavior check. For `public = identity`, `ecdh = identity` regardless of the secret scalar.
- `cipher()` (`encryption.rs:101-133`) derives the ChaCha20 key solely from `context` and the (public, known) identity encoding, with a fixed IV `"DKG IV v0.2\0"`. Anyone can reconstruct this keystream.

Two attack paths exist:

1. A participant registers `enc_key = identity` via `EncryptionKeyMessage`. Every `EncryptedMessage` sent to them is then encrypted under a keystream any observer of the DKG transcript can compute, since the sender computes `ecdh(key_scalar, identity) = identity`. The blame mechanism (`decrypt_with_proof`, `encryption.rs:366-397`) becomes moot — the ciphertext is already plaintext-equivalent to eavesdroppers.
2. A sender submits `EncryptedMessage` with `key = identity` and a forged PoP (`s = r`, since `A = identity` makes `s·G = R + c·identity = R` trivially satisfiable). `Encryption::decrypt` (`encryption.rs:469-501`) computes `ecdh(enc_key, identity) = identity` and returns a "decryption" plus an `EncryptionKeyProof` whose `key` is the identity — meaning the published blame proof reveals nothing secret yet the message decrypts to attacker-chosen bytes, corrupting the share the victim believes they received.

### Impact Explanation
The DKG's confidentiality layer can be silently bypassed. Path 1 turns all shares addressed to the malicious participant into public data — any party observing the authenticated channel learns the share without the participant's cooperation, and the per-message key design (intended so revealing one message can't reveal others, `encryption.rs:78-90`) is defeated. Path 2 lets a sender deliver a deterministic, attacker-chosen "share" while still producing a structurally valid `EncryptionKeyProof`, undermining the blame/confidentiality invariants the encryption layer exists to provide.

### Likelihood Explanation
Any participant in a PedPoP session can send an `EncryptionKeyMessage` or `EncryptedMessage`; both are attacker-controlled bytes fed to `read`. No collusion, no malformed encodings, and no spec violation is needed — the decoder accepts identity today. The only mitigation is that a single share alone doesn't break the threshold, which is why this is not Critical; however, combined with observers of the channel it weakens the confidentiality assumption the DKG relies on.

### Recommendation
Reject the identity point in `EncryptionKeyMessage::read` and `EncryptedMessage::read` (either by switching to a `read_G` variant with an identity check, as `Curve::read_G` does in `crypto/frost/src/curve/mod.rs:125-131`, or by an explicit `is_identity` check), and in `ecdh()` reject/zero-check the computed shared secret before use. Optionally bind `enc_key` with a proof of possession at registration so a participant cannot register a degenerate or borrowed key.

### Proof of Concept
```rust
// In a PedPoP session with Ciphersuite C:
// 1. Malicious participant serializes EncryptionKeyMessage with enc_key = identity:
let mut msg_bytes = underlying_msg.serialize();
msg_bytes.extend(C::G::identity().to_bytes().as_ref()); // enc_key = identity
let ekm = EncryptionKeyMessage::<C, _>::read(&mut msg_bytes.as_slice(), params).unwrap();
// Accepted: C::read_G never rejects identity.

// 2. An honest sender encrypts a share to them:
//    ecdh(key_scalar, identity) == identity  (encryption.rs:95)
//    cipher(context, identity) -> publicly computable ChaCha20 keystream
// Any observer recomputes:
let mut t = RecommendedTranscript::new(b"DKG Encryption v0.2");
t.append_message(b"context", context);
t.domain_separate(b"encryption_key");
t.append_message(b"shared_key", C::G::identity().to_bytes());
let mut key = Cc20Key::default();
key.copy_from_slice(&t.challenge(b"key")[..32]);
let mut iv = Cc20Iv::default();
iv.copy_from_slice(b"DKG IV v0.2\0");
ChaCha20::new(&key, &iv).apply_keystream(&mut ciphertext_copy); // recovers the share
```