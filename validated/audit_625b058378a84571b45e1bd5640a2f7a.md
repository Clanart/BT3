### Title
PedPoP `EncryptedMessage`/`EncryptionKeyMessage` deserialization accepts the identity point, making ECDH-derived keys publicly computable and exposing DKG secret shares to any observer - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptedMessage::read` and `EncryptionKeyMessage::read` deserialize group elements via `Ciphersuite::read_G`, which enforces canonical encoding but does not reject the identity point. The PedPoP share-encryption scheme derives its ChaCha20 key solely from `ecdh(private, public) = public * private` plus the public `context`. If either the per-message ephemeral `key` or a participant's registered `enc_key` is the identity point, the ECDH result is the identity element for every party, so the encryption key is fully determined by public data. An attacker-controlled sender (or an attacker registering an identity `enc_key` in their commitments message) therefore produces "encrypted" secret shares that decrypt correctly for the intended recipient yet are equally decryptable by any third party observing the wire.

### Finding Description
`EncryptedMessage::read` reads `key` with `C::read_G(reader)?` at `crypto/dkg/pedpop/src/encryption.rs:173`, and `EncryptionKeyMessage::read` reads `enc_key` the same way at `crypto/dkg/pedpop/src/encryption.rs:58`. `C` here is only bounded by `Ciphersuite`, whose `read_G` (`crypto/ciphersuite/src/lib.rs:91-101`) checks canonicity but never calls `is_identity`. The identity-rejecting `read_G` exists only on `frost::Curve` (`crypto/frost/src/curve/mod.rs:125-131`), which PedPoP does not use.

The proof-of-possession does not save this: `pop` is a Schnorr signature verified as `sG == R + cA` (`crypto/schnorr/src/lib.rs:88-110`). For `A = identity`, this reduces to `sG == R`, which is trivially satisfied by choosing any `s = r` and `R = rG`. The challenge binds `key` into `pop_challenge` (`encryption.rs:302-324`), so the forged PoP verifies cleanly.

With `key = identity`:
- `ecdh::<C>(&self.enc_key, msg.key)` returns `identity` (`encryption.rs:95-97`, called at `encryption.rs:487`).
- `cipher` (`encryption.rs:101-133`) derives the ChaCha20 key from the transcript of `context` (public `[u8; 32]`) and `shared_key = identity.to_bytes()` — both public constants. The IV is the static `b"DKG IV v0.2\0"`.

The sender constructs their side identically: `ecdh(key=0, to) = identity`. So the message decrypts to a *valid* share — `calculate_share` (`crypto/dkg/pedpop/src/lib.rs:463-499`) verifies the decrypted share against the sender's commitments and it passes — while every byte of the ciphertext is decryptable by anyone who saw the message.

The symmetric variant: `EncryptionKeyMessage::read` accepting `enc_key = identity` means all shares encrypted *to* that participant use `ecdh(ephemeral_scalar, identity) = identity` (`encryption.rs:466` → `encrypt` → `ecdh` at `encryption.rs:154`), so all shares addressed to the attacker are world-decryptable too.

The same class gap applies to `Commitments::read` (`crypto/dkg/pedpop/src/lib.rs:110-128`), `EncryptionKeyProof::read` (`encryption.rs:267-269`), and `GeneratorProof::read` (`crypto/dkg/promote/src/lib.rs:72-77`), which all use the non-identity-rejecting `Ciphersuite::read_G`.

### Impact Explanation
The DKG encrypts shares precisely because the transport channel is authenticated but not confidential (the code comments at `encryption.rs:300-301` acknowledge there is no trusted channel beyond authentication). An unprivileged attacker participating in the DKG — or observing/coordinating the message flow — submits normally-formed bytes to `EncryptedMessage::read`. By sending shares with `key = identity`, the attacker publishes `n - 1` valid secret shares in effective plaintext; by registering `enc_key = identity`, the attacker causes all `n - 1` shares addressed to them to be world-readable. Any observer who collects `t` shares interpolates the group secret key, yielding complete recovery of the threshold key without breaking a single signature check. This is silent key compromise: the protocol completes successfully, `ThresholdKeys` are produced, and every subsequent FROST signature is made over a key the observer controls.

### Likelihood Explanation
Reachability is direct: `EncryptedMessage::read` is on the documented untrusted-input list and is invoked on peer-supplied bytes in `processor/src/key_gen.rs:409` before `calculate_share`. Exploitation requires only sending a well-formed, canonically-encoded identity point (e.g., the all-zero Ristretto encoding) — no hash grinding, no race, no collusion. The PoP for the identity key is a trivial forgery computable offline. The only precondition is that share ciphertexts transit a non-confidential channel, which is the design assumption motivating this encryption layer in the first place.

### Recommendation
Reject identity points wherever a deserialized `C::G` is used as a key-agreement or key public input. Concretely:
- In `EncryptedMessage::read` (`encryption.rs:171-177`) and `EncryptionKeyMessage::read` (`encryption.rs:57-59`), add `if bool::from(point.is_identity()) { Err(...) }` after `C::read_G` — or route deserialization through an identity-rejecting helper equivalent to `frost::Curve::read_G`.
- Apply the same check in `Commitments::read`, `EncryptionKeyProof::read`, and `GeneratorProof::read`, since identity commitments/keys are never legitimate in PedPoP.
- Defense-in-depth: assert in `ecdh`/`cipher` that the shared point is not identity, so a zero ECDH output can never silently produce a deterministic public key.

### Proof of Concept
Conceptual, against `EncryptedMessage::read`:

1. Attacker (participant `l`) prepares a valid secret share `share` for recipient `j` per their polynomial.
2. Instead of calling `encrypt`, the attacker builds `EncryptedMessage { key: identity, pop: SchnorrSignature { R: r*G, s: r }, msg: XOR(share_repr, keystream) }` where `keystream = ChaCha20::new(H(context || "encryption_key" || identity_bytes), b"DKG IV v0.2\0")`. Any observer can compute the same keystream.
3. `pop.verify(identity, pop_challenge(...))` passes because `sG = rG = R` and `c*identity = identity`.
4. `j` calls `EncryptedMessage::read` → `decrypt`: `ecdh(enc_key_j, identity) = identity`, decryption succeeds, `C::F::from_repr(share)` succeeds, `share_verification_statements` multiexp is identity — share accepted.
5. An eavesdropper who logged the wire bytes repeats step 2's keystream derivation and recovers `share`. Repeat for all `n - 1` recipients (or have the attacker register `enc_key = identity` so all shares *to* them are exposed); interpolate any `t` shares → group private key recovered.

Supporting code: `crypto/dkg/pedpop/src/encryption.rs:95-97` (unauthenticated ECDH with no identity check), `encryption.rs:171-177` (`key: C::read_G` accepts identity), `encryption.rs:487-488` (identity shared point → deterministic cipher), `crypto/ciphersuite/src/lib.rs:91-101` (`read_G` checks canonicity only, no `is_identity`), `crypto/schnorr/src/lib.rs:108-110` (PoP verifiable for identity key).