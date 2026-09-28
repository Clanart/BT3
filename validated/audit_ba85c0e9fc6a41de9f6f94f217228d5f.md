I'll check key spots: `ThresholdKeys::new` validation, musig verification, and how PedPoP handles identity points.### Title
PedPoP accepts the identity point as an encryption key, producing a publicly known ECDH shared secret - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2022-23004 describes a scalar multiplication / shared-secret computation which, given a degenerate public key (X = 0), produces an invalid result instead of a proper rejection. The analog in Serai is PedPoP's ECDH: encryption keys (`EncryptionKeyMessage.enc_key`) and per-message keys (`EncryptedMessage.key`) are parsed with `Ciphersuite::read_G`, which only checks canonicality and does **not** reject the identity point (`crypto/ciphersuite/src/lib.rs:91-101`; only `Curve::read_G` adds the identity check, `crypto/frost/src/curve/mod.rs:125-131`). An identity key makes `ecdh()` return the identity group element (`crypto/dkg/pedpop/src/encryption.rs:95-97`), and `cipher()` derives the ChaCha20 keystream solely from the public `context` plus `ecdh.to_bytes()` (`encryption.rs:101-133`) — a keystream any third party can recompute.

### Finding Description
- `Decryption::register` stores `msg.enc_key` with no validity check beyond `C::read_G` (`encryption.rs:57-59`, `351-362`).
- `Encryption::encrypt` computes `cipher(context, &ecdh(&key, self.decryption.enc_keys[&participant]))` (`encryption.rs:154`, `460-467`). With `enc_key == identity`, `ecdh == identity` for every message to that participant, regardless of the random per-message key.
- `EncryptedMessage::read` likewise accepts `key = identity` (`encryption.rs:171-177`), and the Schnorr PoP over `msg.key` is satisfied by any `(R = sG, s)` since `batch_statements` reduces to `R - sG == 0` when `public_key` is the identity (`crypto/schnorr/src/lib.rs:88-99`). So a sender can also deliver a message whose decryption key is the publicly known identity point.

### Impact Explanation
Key share recovery. Secret shares are exchanged over authenticated — not confidential — channels; this ChaCha20 layer is the only confidentiality mechanism (spec: `spec/cryptography/Distributed Key Generation.md`). A malicious participant who registers `enc_key = identity` causes every honest participant to encrypt that participant's shares under `keystream(context, identity)`, which the attacker — and any passive observer of the DKG transcript (e.g., shares relayed via a public coordinator/mempool) — can recompute and decrypt.

Concretely, with `t = 1` (`ThresholdParams::new(1, n, i)` is permitted by `crypto/dkg/src/lib.rs` and `dealer`), each polynomial is constant, so the shares `f_i(j)` sent to the malicious participant are the contributors' full secret coefficients. A passive observer decrypts all of them and recovers the group secret key as `Σ coefficients_i`, verified against `group_key = Σ commitments[i][0]`. For `t > 1`, each participant that registers an identity key leaks its own aggregate share, weakening the scheme to any observer who obtains `t` such leaked shares.

The PoP bypass (`key = identity` on `EncryptedMessage`) additionally lets a sender deliver ciphertext whose plaintext is arbitrary-but-known to the sender while remaining fully "valid" — though the larger issue is the confidentiality collapse on the registration side.

### Likelihood Explanation
An unprivileged DKG participant reaches this purely through `EncryptionKeyMessage::read` → `C::read_G` accepting the canonical encoding of the identity point (e.g., `01 00…00` for Ristretto/ed25519 compressed identity, `02/03` infinity encodings rejected but identity-on-curve encodings accepted for k256/p256 where applicable). No collusion, no privileged position, and no cryptographic assumption is needed — it is a single crafted 32/33-byte field in the first DKG round.

### Recommendation
Reject the identity point in `EncryptionKeyMessage::read`, `EncryptedMessage::read`, and `EncryptionKeyProof::read` (or centrally in `Decryption::register` and `Encryption::encrypt`/`decrypt`), e.g., mirror `Curve::read_G`'s `is_identity` check. Optionally also reject low-order points. This prevents degenerate ECDH results from silently collapsing the cipher to a publicly computable keystream.

### Proof of Concept
```rust
// Attacker's round-1 message inside KeyGenMachine::generate_coefficients flow:
// Serialization of EncryptionKeyMessage<C, Commitments<C>> where
// enc_key is the canonical encoding of the identity point.
let mut msg_bytes = honest_commitments.serialize();
msg_bytes.extend(<Ristretto as Ciphersuite>::G::identity().to_bytes()); // 0x01 followed by 31 zero bytes? -> identity encoding
let msg = EncryptionKeyMessage::<Ristretto, Commitments<Ristretto>>::read(
    &mut msg_bytes.as_ref(), params,
).unwrap(); // accepted: C::read_G performs no identity check

// Honest victim's machine then executes:
//   encryption.encrypt(rng, l, share_bytes)
//     -> ecdh(&random_key, identity) == identity
//     -> cipher(context, identity)  == ChaCha20 keystream derivable by anyone
// The transmitted EncryptedMessage msg field is share XOR public_keystream,
// so any observer recovers `share_bytes` = the secret share for that participant.
```
Root cause lines: `crypto/dkg/pedpop/src/encryption.rs:58` (`enc_key` read without identity check), `:95-97` (`ecdh` returns the point unchanged — identity), `:101-133` (`cipher` keyed only by context + ecdh bytes), and `crypto/ciphersuite/src/lib.rs:91-101` (canonicality-only `read_G`).