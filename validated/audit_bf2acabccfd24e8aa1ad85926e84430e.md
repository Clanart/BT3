### Title
Unvalidated identity encryption key in PedPoP `EncryptionKeyMessage` makes ECDH shared key publicly computable, exposing DKG secret shares to passive observers - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The external report describes a credential-derived attribute (X.509 role) bypassing its allow-list validation on a specific transport path. The Serai analog: `EncryptionKeyMessage::read` deserializes the per-participant ECDH encryption key with `Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-101`), which enforces canonicity but deliberately does **not** reject the identity point — unlike `Curve::read_G` (`crypto/frost/src/curve/mod.rs:125-131`), which exists precisely because identity is dangerous. When a malicious participant registers `enc_key = identity`, the ECDH result `ecdh(private, identity)` is the identity element for every sender (`crypto/dkg/pedpop/src/encryption.rs:95-97`), so the ChaCha20 cipher key derived in `cipher()` (`encryption.rs:101-133`) is computed solely from public data (`context`, the identity encoding) and is recoverable by anyone.

### Finding Description
`Decryption::register` (`encryption.rs:351-362`) stores `msg.enc_key` per participant with no validity check beyond the canonical-point check inside `read`. `Encryption::encrypt` (`encryption.rs:460-467`) then computes `ecdh(&key, self.decryption.enc_keys[&participant])`. With `enc_keys[l] = identity`, `ecdh = identity` regardless of the ephemeral `key`, so `cipher(context, identity)` is a public keystream. Every `EncryptedMessage<C, SecretShare>` produced by `SecretShareMachine::generate_secret_shares` destined for participant `l` (`pedpop/src/lib.rs:357-370`) is therefore effectively plaintext to any observer of the authenticated channel. The PoP signature check cannot catch this — no signature covers `enc_key` at all; the Schnorr PoK in `verify_r1` (`pedpop/src/lib.rs:321-334`) binds only `commitments` and the sender index, never `enc_key`.

The same missing identity check applies to `EncryptedMessage::key` and `EncryptedMessage::pop`'s verification key (`encryption.rs:170-177`, `469-501`): a message with `key = identity` satisfies `pop.verify(identity, ...)` trivially (choose `s`, set `R = s·G`), again demonstrating that the identity validation the FROST layer requires is absent on this DKG path.

### Impact Explanation
The encryption layer exists because the DKG's channel is only authenticated, not confidential (`encryption.rs:299-301`). With a forged `enc_key = identity`, an unprivileged passive observer who sees the `EncryptedMessage` ciphertexts re-derives the cipher and recovers all `n - 1` secret shares delivered to participant `l`, i.e. l's complete `ThresholdKeys` secret share — key share material recovered by a party holding no share at all. For `t`-of-`n` with small `t`, or combined with a single additional leaked/compromised share, this directly undermines the threshold security of the resulting group key. This is the same failure mode as the MongoDB CVE: a security attribute (here, "the ECDH key must be a non-trivial element with unknown discrete log") is accepted unchecked on a specific input path, silently downgrading the protection it was supposed to enforce.

### Likelihood Explanation
Fully reachable from public inputs: the malicious participant only broadcasts a crafted `EncryptionKeyMessage` (bytes fed to `EncryptionKeyMessage::read` / `Commitments::read`) during round 1 of `KeyGenMachine` — no collusion, no validator privileges. `C::read_G` accepts the canonical identity encoding on every in-scope ciphersuite (`PrimeGroup::from_bytes` accepts identity; only the re-serialization check runs). All honest participants then voluntarily encrypt their shares to the attacker's public-key-equivalent "key". Deterministic, no probabilistic requirements.

### Recommendation
Reject the identity point wherever a group element carries an authentication/confidentiality role on a deserialized path:

- In `EncryptionKeyMessage::read` (`encryption.rs:57-59`) and `EncryptedMessage::read` (`encryption.rs:171-177`), reject `is_identity()` after `C::read_G`, mirroring `Curve::read_G`.
- Equivalently, validate in `Decryption::register` (`encryption.rs:356`) that `msg.enc_key` is non-identity before insertion.
- Audit the other `Ciphersuite::read_G` consumers for the same gap: `Commitments::read` (`pedpop/src/lib.rs:115-124`), `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:620-623`), `SchnorrSignature::read` (`crypto/schnorr/src/lib.rs:51-53`), `DLEqProof::read`, and `SchnorrAggregate::read` (`crypto/schnorr/src/aggregate.rs:77-88`).

### Proof of Concept
```rust
// Attacker l=2 participates in KeyGenMachine with context C, params (t, n).
// Instead of Encryption::registration(), attacker crafts:

let mut msg = EncryptionKeyMessage::<Secp256k1, Commitments<Secp256k1>> {
    msg: attacker_commitments,               // valid PoK over commitments only
    enc_key: Secp256k1::G::identity(),       // IDENTITY — passes Ciphersuite::read_G
};
let bytes = msg.serialize();                 // canonical encoding, accepted by read()

// Every honest participant i calls:
//   self.encryption.register(Participant(2), EncryptionKeyMessage::read(bytes))
// storing enc_keys[2] = identity (no check).

// Honest i then runs:
//   encrypt(rng, Participant(2), share_i)
//     ecdh(key_i, identity) == identity
//     cipher(context, identity) — ChaCha20 key = transcript(context, identity.to_bytes())
// Both inputs are public. A passive observer recomputes cipher() and decrypts
// EncryptedMessage<C, SecretShare>::msg, recovering share_i(2) for every sender i.
// Sum of recovered shares = participant 2's complete secret share in ThresholdKeys.
```

Caveat: the leaked material is one participant's aggregate share; exploitation to full signing requires the observer to additionally reach the threshold (trivially satisfiable for `t = 1`, or in conjunction with the attacker's own share if the attacker is participant `l` itself — in which case the observer obtains an independent copy of an attacker-controlled share at no cost). The vulnerability is in the silent, path-specific absence of the validation, matching the reported bug class.