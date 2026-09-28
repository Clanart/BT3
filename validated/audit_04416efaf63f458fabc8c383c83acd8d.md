### Title
Insecure deserialization of ECDH encryption keys accepts the identity point, forcing a publicly-computable shared secret that leaks DKG secret shares - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The blind-XXE class (untrusted input processed by an insecure parser/decoder, enabling data exfiltration) maps onto Serai's untrusted-byte deserialization boundary. `EncryptionKeyMessage::read` and `EncryptedMessage::read` in PedPoP parse attacker-supplied elliptic-curve points through `Ciphersuite::read_G`, which enforces canonical encoding and subgroup membership but does **not** reject the identity point. These points are used directly as ECDH public keys to derive the ChaCha20 keystream protecting threshold secret shares. An identity `enc_key` (or message `key`, whose PoP is trivially forgeable) forces the ECDH result to the identity element, making the encryption keystream computable by anyone — a classic forced-identity/small-subgroup deserialization flaw.

### Finding Description
`Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) validates canonical encoding only. Unlike `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`), it does not reject `is_identity`. For `dalek-ff-group` points, `from_bytes` accepts the identity (only torsion is banned) (`crypto/dalek-ff-group/src/lib.rs:429-437`).

Two reachable sinks consume these points without any identity check:

1. `EncryptionKeyMessage::read` reads `enc_key` via `C::read_G` (`crypto/dkg/pedpop/src/encryption.rs:57-59`). `Decryption::register` stores it unchecked (`encryption.rs:351-362`), and `Encryption::encrypt` computes `ecdh(&key, to)` where `to` is this point (`encryption.rs:95-97`, `466`). If a participant registers `enc_key = identity`, every honest sender's ECDH output is `identity`, so `cipher(context, identity)` (`encryption.rs:101-133`) produces a keystream derivable by any observer who knows the public context.

2. `EncryptedMessage::read` reads `key` via `C::read_G` (`encryption.rs:171-177`). Its Schnorr PoP is forgeable for the identity key: with secret `x = 0`, `s = r`, `R = rG` satisfies `sG == R + c·identity`, so `SchnorrSignature::verify` (`crypto/schnorr/src/lib.rs:108-110`) passes. The receiver's `ecdh(&enc_key, msg.key)` is then `identity`, giving the same public keystream.

The ciphertext bytes (`msg`) are fixed-length encrypted shares read via `E::read` — exactly the "untrusted bytes → read/verify API" surface the threat model covers.

### Impact Explanation
PedPoP relies on this ChaCha20 layer to keep each participant's polynomial evaluations `f_i(j)` confidential; only the sum at each index should be learnable by that index's owner. With an identity encryption key, **any network observer** (PedPoP assumes an authenticated but non-confidential broadcast channel, per the comments at `encryption.rs:299-301`) computes `ChaCha20(key = H(context ‖ "shared_key"=identity), iv = "DKG IV v0.2")` and recovers every honest sender's individual share `f_i(j)` destined for the malicious index `j`, plus the combined secret share at `j`. This yields key-share recovery for index `j` to parties who never participated, degrading the effective threshold: an eavesdropper collecting shares at enough compromised-or-forced indices can reconstruct the group secret without being a signer. It also silently undermines the blame/reveal protocol, since the revealed `EncryptionKeyProof.key` is already public.

### Likelihood Explanation
Any unprivileged PedPoP participant (or any sender of an `EncryptedMessage`) reaches this with pure public inputs: submit `enc_key` = identity encoding in the `EncryptionKeyMessage` broadcast, or `key` = identity with a PoP where `s = r`. No collusion, no malformed encodings, and no invalid curve points are needed — the bytes are canonical, torsion-free, and pass every existing check. The only mitigating factor is that a single forced index leaks shares at one index; reconstructing the master secret still requires `t` indices, so this is a partial rather than complete break on its own.

### Recommendation
Reject the identity point wherever a deserialized point is used as a public key/ECDH input:
- In `EncryptionKeyMessage::read` and `EncryptedMessage::read`, check `is_identity()` on `enc_key`/`key` (or use a `Curve::read_G`-style identity-rejecting read).
- Consider rejecting identity in `SchnorrSignature`'s `R`/`public_key` verification path for PoP usage, or explicitly check `msg.key.is_identity()` in `Encryption::decrypt`/`decrypt_with_proof` before computing the ECDH.
- Add regression tests asserting that identity `enc_key`/`key` are rejected on `read`.

### Proof of Concept
```rust
// Attacker (participant j) registers an identity encryption key.
// Ristretto example: identity encoding is 32 zero bytes.
let identity_enc = Ristretto::read_G(&mut [0u8; 32].as_ref()).unwrap(); // succeeds: canonical, torsion-free
let msg = EncryptionKeyMessage { msg: their_commitments, enc_key: identity_enc };
// Broadcast; every honest sender calls:
//   encrypt(rng, ctx, i, to = identity, share_i_j)
//   ecdh = share_ephemeral_scalar * identity = identity
//   cipher key = H("DKG Encryption v0.2" ‖ ctx ‖ "encryption_key" ‖ identity_bytes)
// Any observer recomputes this keystream and XORs it against
// EncryptedMessage.msg, recovering f_i(j) for all honest i.

// Sender-side variant: EncryptedMessage with key = identity.
// PoP: pick r, set R = rG, s = r. Then sG == R + c*0 == R. verify passes.
// Receiver computes ecdh(enc_priv, identity) = identity → same public keystream.
```