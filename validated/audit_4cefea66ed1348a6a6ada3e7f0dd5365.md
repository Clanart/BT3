### Title
PedPoP accepts identity public encryption keys, silently dropping share confidentiality to a publicly derivable key - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
CVE-2017-12151 describes a connection that silently loses its encryption/signing requirement on a protocol path (DFS redirect under SMB3). The Serai analog lives in the PedPoP DKG: a participant's long-term encryption public key (`enc_key` inside `EncryptionKeyMessage`) is accepted by `C::read_G` and stored by `Decryption::register` with only a canonical-encoding check — no rejection of the identity point and no proof of possession. On prime-order-exposed groups where the identity has a valid canonical encoding (Ristretto, Ed25519 in `dalek-ff-group`), registering `enc_key = identity` makes every ECDH shared point equal to `identity`, so the ChaCha20 key used to "encrypt" secret shares is derived solely from the public `context` and the publicly known identity encoding — a connection that looks encrypted but is keyed by public data.

### Finding Description
- `EncryptionKeyMessage::read` parses `enc_key` via `C::read_G`, which only checks that `from_bytes` succeeds and the encoding is canonical; it never rejects the identity point. `crypto/ciphersuite/src/lib.rs:91-101`, `crypto/dkg/pedpop/src/encryption.rs:57-59`.
- `Decryption::register` stores the key unconditionally (`self.enc_keys.insert(participant, msg.enc_key)`), with only a duplicate-registration assert. `crypto/dkg/pedpop/src/encryption.rs:351-362`.
- `Encryption::encrypt` then encrypts the secret share under `self.decryption.enc_keys[&participant]` via `ecdh(&key, to)` = `key * to`. `crypto/dkg/pedpop/src/encryption.rs:95-97,466`.
- `cipher` derives the ChaCha20 key from `RecommendedTranscript("DKG Encryption v0.2")` over only `context` and the ECDH point bytes, with a fixed IV `"DKG IV v0.2\0"`. `crypto/dkg/pedpop/src/encryption.rs:101-133`.
- `context` is a public per-multisig value supplied by the caller (`KeyGenMachine::new(params, context)`; the processor derives it from session data, not from anything secret). `crypto/dkg/pedpop/src/lib.rs:144-150`.

If `to == identity`, the ECDH result is `identity`, and the cipher keystream is `ChaCha20(Transcript("DKG Encryption v0.2" | context | "encryption_key" | identity_encoding).challenge("key"), fixed_iv)` — computable by anyone who knows the multisig context. The `EncryptedMessage` wire format (`key`, `pop`, ciphertext) is then a publicly-decryptable blob: the per-message PoP over `msg.key` verifies fine since the sender honestly generated it, so nothing in `decrypt`, `calculate_share`, or `blame` detects the downgrade. `crypto/dkg/pedpop/src/encryption.rs:374-397,479-501`.

The spec acknowledges the adjacent "co-opted key" attack and defends it with the per-message PoP, but the identity-key downgrade is not addressed. `spec/cryptography/Distributed Key Generation.md:19-35`.

### Impact Explanation
Secret-share confidentiality is the only thing protecting each participant's polynomial evaluations on the wire. With `enc_key = identity` registered for participant `l`, every `EncryptedMessage<C, SecretShare>` addressed to `l` decrypts with publicly derivable keystreams, so any observer recovers all shares sent to `l` and sums them into `l`'s full `ThresholdKeys` secret share — the same value `calculate_share` produces internally (`*self.secret += share`). `crypto/dkg/pedpop/src/lib.rs:479-484`. For low thresholds this is catastrophic: in a 2-of-n multisig, one malicious participant who registers the identity key plus observes the wire (or simply is the network layer) recovers the victim's share and, combined with their own, the full group private key — unilateral spendability of all funds. Even at higher thresholds it is unambiguous secret-share recovery by a party that was only supposed to hold ciphertext. This matches the CVE's shape: the channel appears to carry an encrypted share, but the encryption requirement has silently evaporated for that hop.

### Likelihood Explanation
Fully reachable by an unprivileged participant with public inputs: they serialize `EncryptionKeyMessage { msg: Commitments{...}, enc_key: <identity encoding> }` in round 1; `EncryptedMessage::read`/`EncryptionKeyMessage::read` accept it, `verify_r1` only batch-verifies the coefficient PoK (`msg.sig.batch_verify` over `msg.commitments[0]`), and no check on `enc_key` exists anywhere in `verify_r1`, `register`, `encrypt`, or `decrypt`. `crypto/dkg/pedpop/src/lib.rs:313-332`. Applies at minimum to the Ristretto substrate key-generation path (`EncryptionKeyMessage::<Ristretto, Commitments<Ristretto>>::read` in `processor/src/key_gen.rs:268`) and to Ed25519, since compressed identity encodings round-trip canonically in `dalek-ff-group`. Whether `k256`/`secp256k1` accepts an identity encoding was not verified in this iteration; if rejected there, the finding still stands on the Ristretto leg alone.

### Recommendation
Reject non-contributory/identity points in `Decryption::register` / `EncryptionKeyMessage::read` (e.g., `if bool::from(msg.enc_key.is_identity()) { Err(...) }`), and consider requiring a proof of possession for `enc_key` itself so a registered key is bound to a known discrete log — closing both the identity downgrade and any small-subgroup equivalent on other ciphersuites.

### Proof of Concept
```rust
// Malicious participant l in a PedPoP DKG over Ristretto:
// 1. Build a normal Commitments message, then serialize EncryptionKeyMessage with
//    enc_key = RistrettoPoint::identity() (32 zero bytes — canonical for Ristretto).
let mut msg_bytes = commitments.serialize();
msg_bytes.extend(<Ristretto as Ciphersuite>::G::identity().to_bytes()); // enc_key = identity

// 2. Honest peers call EncryptionKeyMessage::read -> Ok; Decryption::register stores it.
// 3. Each sender calls encryption.encrypt(rng, l, share_bytes):
//      ecdh(&key, identity) == identity
//      cipher key = challenge over ("DKG Encryption v0.2", context, identity.to_bytes())
//      ciphertext = share_bytes XOR keystream

// 4. Observer (or malicious co-participant reading the wire) recovers the share:
let mut t = RecommendedTranscript::new(b"DKG Encryption v0.2");
t.append_message(b"context", context);           // public multisig context
t.domain_separate(b"encryption_key");
t.append_message(b"shared_key", <Ristretto as Ciphersuite>::G::identity().to_bytes().as_ref());
let mut key = Cc20Key::default();
key.copy_from_slice(&t.challenge(b"key")[..32]);
let mut iv = Cc20Iv::default();
iv.copy_from_slice(b"DKG IV v0.2\0");
let mut share_bytes = ciphertext.clone();
ChaCha20::new(&key, &iv).apply_keystream(&mut share_bytes);
let share = Scalar::from_repr(share_bytes).unwrap(); // victim's secret share, recovered
```