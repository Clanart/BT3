### Title
Unvalidated ECDH public key in DKG registration allows identity-key attack leaking other participants' secret shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The `value-censorship` bug class — failure to validate attacker-supplied inputs at object-construction time, letting those inputs escape their intended sandbox — maps onto Serai's DKG encryption layer: `EncryptionKeyMessage::read` and `Decryption::register` accept a participant's encryption public key (`enc_key`) without validating it is a non-identity point. A malicious DKG participant registers the group identity as their `enc_key`, causing every honest participant's ECDH shared secret for messages addressed to them to collapse to the public constant `key * 0 = identity`. The resulting ChaCha20 key is derivable by anyone, so the encrypted secret shares broadcast to that participant are decryptable by any observer.

### Finding Description
`EncryptionKeyMessage::read` at crypto/dkg/pedpop/src/encryption.rs:57-59 reads `enc_key` via `C::read_G(reader)?`. `C::read_G` (crypto/ciphersuite/src/lib.rs:91-101) only rejects non-canonical encodings; the identity point is a canonical, valid group element and passes. `Decryption::register` (encryption.rs:351-362) inserts `msg.enc_key` into `enc_keys` with no identity/non-triviality check, and there is no proof-of-possession requirement on the registered key.

During share distribution, `Encryption::encrypt` (encryption.rs:460-467) calls `encrypt`, which computes `ecdh::<C>(&key, to)` = `to * key` (encryption.rs:95-97, 154). If `to` is the identity, the shared secret is the identity for every sender and every message. `cipher` (encryption.rs:101-133) derives the ChaCha20 key solely from the public `context` and the shared-key encoding — both now public values — with a fixed IV (`b"DKG IV v0.2\0"`). Any observer who sees the broadcast `EncryptedMessage` can reconstruct the keystream and recover the plaintext share intended for the malicious participant.

### Impact Explanation
The encrypted messages carry DKG secret shares addressed to individual participants. An attacker who already holds their own share and tricks honest parties into encrypting to an identity key obtains other participants' shares from the broadcast ciphertexts. With `t` known shares (their own plus leaked ones), the attacker interpolates and recovers the full group secret key — complete collapse of the threshold assumption, enabling unilateral forging of signatures for the group key. This is exactly the "identity or reused commitments / ECDH" failure the PedPoP construction is meant to defend against, and it is reachable purely from messages an unprivileged participant broadcasts during the DKG.

### Likelihood Explanation
Any DKG participant controls their own `EncryptionKeyMessage` bytes. Exploitation requires only serializing the identity point encoding as `enc_key`; `read_G` and `register` impose no further checks, and the PoP protection (`pop` field in `EncryptedMessage`) exists on per-message ephemeral keys, not on the registered long-term `enc_key`. No collusion, timing, or special privileges are needed. The main limitation is that the attack leaks shares *sent to the attacker*, so recovering the secret still requires the attacker to gather `t` shares total — feasible whenever `t >= 2` and the attacker participates, since each victim contributes one additional share.

### Recommendation
Validate `enc_key` in `EncryptionKeyMessage::read` or `Decryption::register`: reject the identity point (e.g., `enc_key.is_identity()` / `enc_key == C::G::identity()`) and, for non-prime-order curves, reject low-order/torsion components. Require a proof of possession of the discrete logarithm of `enc_key` at registration so the key is bound to a known scalar. The same identity check should be applied to `msg.key` in `EncryptedMessage::read` to prevent degenerate ECDH outputs on the decrypt path.

### Proof of Concept
```rust
// Attacker (participant j) during key registration:
let mut msg = Vec::new();
attacker_msg_payload.write(&mut msg).unwrap();          // inner M payload
// enc_key = identity encoding, e.g. for secp256k1/ed25519 the canonical identity bytes
msg.write_all(C::G::identity().to_bytes().as_ref()).unwrap();
let ekm = EncryptionKeyMessage::<C, M>::read(&mut msg.as_slice(), params).unwrap(); // accepted
victim.register(attacker_j, ekm);                       // no identity check -> enc_keys[j] = identity

// Victim i then sends their share for j:
// encrypt() computes ecdh = enc_keys[j] * k_i = identity * k_i = identity
// cipher key = transcript(context || identity.to_bytes()) -> publicly computable

// Eve (or the attacker) observing the broadcast EncryptedMessage:
let mut transcript = RecommendedTranscript::new(b"DKG Encryption v0.2");
transcript.append_message(b"context", context);
transcript.domain_separate(b"encryption_key");
transcript.append_message(b"shared_key", C::G::identity().to_bytes());
let mut key = Cc20Key::default();
key.copy_from_slice(&transcript.challenge(b"key")[..32]);
let mut iv = Cc20Iv::default();
iv.copy_from_slice(b"DKG IV v0.2\0");
ChaCha20::new(&key, &iv).apply_keystream(&mut captured_ciphertext); // recovers j's plaintext share
```