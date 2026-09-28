### Title
PedPoP accepts identity (zero-discrete-log) encryption keys, making shares sent to that registrant decryptable by anyone - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The ruby-jwt bug is "a degenerate/empty key is silently accepted by the verifier, so a publicly computable MAC authenticates an attacker payload". Serai's analog: `EncryptionKeyMessage::read` (encryption.rs:57-59) and `EncryptedMessage::read` (encryption.rs:171-177) parse group elements with `Ciphersuite::read_G`, which only enforces canonical encoding and does not reject the identity point (crypto/ciphersuite/src/lib.rs:91-101). Only FROST's `Curve::read_G` adds an `is_identity` rejection (crypto/frost/src/curve/mod.rs:125-131). `Decryption::register` (encryption.rs:351-362) stores the key with no non-identity check, and `encrypt` computes the shared secret as `ecdh(key, to) = to * ephemeral` (encryption.rs:95-97, 154). If `to` is identity, the ECDH output is always identity regardless of the ephemeral key, so `cipher` derives a ChaCha20 keystream from publicly known bytes (encryption.rs:101-132). The identity point likewise has a trivially forgeable PoP: with `A = identity`, `SchnorrSignature::verify` reduces to `R == sG` (crypto/schnorr/src/lib.rs:88-110), so anyone satisfies the proof-of-possession gate without a secret — the exact "verification succeeds under a zero-strength key" shape of CVE-2026-45363.

### Impact Explanation
An unprivileged participant publishes an `EncryptionKeyMessage` whose `enc_key` is the canonical identity encoding. Registration succeeds (`register` only asserts non-duplicate). Every honest participant that then encrypts a secret share to that participant produces `ecdh = identity`, and the resulting ChaCha20 key is `transcript.challenge("key")` over known, public bytes. Any passive observer of the broadcast encrypted shares can derive the same keystream and recover every secret share addressed to that participant — recovering victim key shares, the threshold-secret material the DKG exists to protect. Additionally, the forgeable PoP lets an attacker attach a valid-looking `pop` to an `EncryptedMessage` keyed by identity, bypassing the check that is meant to bind ciphertexts to someone who knows the key's discrete log.

### Likelihood Explanation
Reachable entirely with public inputs: the attacker-controlled bytes flow through `EncryptionKeyMessage::read` → `register` → `enc_keys`, and the maliciously chosen key is then used by *honest* senders in `encrypt`/`ecdh`. No collusion, broken BFT, or leaked keys are required — the missing check is a pure precondition omission on untrusted deserialization, identical in structure to the missing `raise InvalidKeyError if key.empty?`.

### Recommendation
Reject the identity point when reading encryption keys and message keys: either use a non-identity-checking `read_G` variant (as `Curve::read_G` does) in `EncryptionKeyMessage::read` and `EncryptedMessage::read`, or add an explicit `is_identity` rejection in `Decryption::register`. Optionally also reject identity in `EncryptionKeyProof::read`/`decrypt_with_proof`, since a proof keyed to identity is meaningless.

### Proof of Concept
1. Malicious participant `i` serializes `EncryptionKeyMessage { msg, enc_key: C::G::identity() }`; `C::read_G` accepts it (canonical encoding of identity round-trips).
2. `register(i, msg)` stores `enc_keys[i] = identity` — no error.
3. Honest sender calls `encrypt(rng, i, share)` → `ecdh(&key, identity) = identity` → `cipher(context, identity)`.
4. Observer computes `cipher(context, identity)` itself (transcript of `b"DKG Encryption v0.2"`, context, and the known identity encoding), applies the keystream to the broadcast ciphertext, and recovers `share` in the clear — key share recovery with no secret material.

*Uncertainty:* the snippet index did not confirm whether upstream `ThresholdKeys`/share-verification elsewhere would later catch an injected or leaked share, but the confidentiality loss of the encrypted share itself is already demonstrated at the `ecdh`/`cipher` layer.