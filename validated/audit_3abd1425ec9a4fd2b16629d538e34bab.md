### Title
PedPoP lets a participant claim another participant's encryption key without proof of possession, leaking that index's secret shares to the key's real owner - ([File: crypto/dkg/pedpop/src/encryption.rs](crypto/dkg/pedpop/src/encryption.rs))

### Summary
The bug class in the external report is a missing ownership/authorization check: `applyToRound` lets anyone submit data attributed to an arbitrary `projectID`. The Serai analog is `Decryption::register` / `EncryptionKeyMessage` in PedPoP: each participant registers an `enc_key` used by all other participants to ECDH-encrypt secret shares to them, but the key carries no proof-of-possession and no uniqueness check. A malicious participant can register a victim's `enc_key` (copied from the victim's own `EncryptionKeyMessage`) under their own participant index, causing every share addressed to the attacker's index to be encrypted to the victim — who can then decrypt and recover the attacker's-index secret shares.

### Finding Description
`EncryptionKeyMessage { msg, enc_key }` is a plain wrapper: `enc_key` is read and stored with no authentication (`encryption.rs` L50-59). In `Decryption::register`, the only check is that the participant hasn't already registered — `enc_key` is inserted verbatim, with no proof-of-possession binding it to `participant` and no check that it isn't already registered under a different index (`encryption.rs` L351-362).

The round-1 PoK does not cover `enc_key`. In `verify_r1`, the Schnorr signature is verified against `msg.commitments[0]` with a challenge over `msg.cached_msg` — the serialized `Commitments` payload — which does not include the `enc_key` field of the outer `EncryptionKeyMessage` (`lib.rs` L323-329, `encryption.rs` L56-59). The per-message PoP added in `encrypt`/`decrypt` (`encryption.rs` L83-91, L479-485) only protects the ephemeral per-message key, not the registered long-term `enc_key`.

Encryption to a participant uses whatever key was registered for their index: `encrypt` ECDHs `self.decryption.enc_keys[&participant]` (`encryption.rs` L466). So if Mallory registers Alice's `enc_pub_key` under Mallory's index `m`, every honest participant's share evaluated at `m` is encrypted under Alice's public key. In Serai's coordinator flow the resulting `DkgShares` bytes are published to the tributary (`coordinator/src/tributary/handle.rs` L361), so Alice can read all ciphertexts "to Mallory", decrypt them with her own `enc_key`, and recover the full combined secret share at index `m` — another participant's index share — without Mallory's cooperation or any fault on her part.

### Impact Explanation
Recovery of a distinct participant index's secret share. Alice ends up holding two valid participant shares (her own index plus the co-opted index `m`), which effectively lets a single party control two of the `n` share indexes. This lowers the real collusion resistance of the threshold key: a coalition of `t-1` parties that includes one beneficiary of this attack reaches `t` shares and can reconstruct the group secret. This matches the "key share recovery" acceptance criterion, and is structurally identical to the referenced issue — data is attributed to an index the submitter doesn't own the credentials for, redirecting value (shares instead of a grant) to a party that never authorized it.

### Likelihood Explanation
Requires only a single malicious DKG participant (a validator) submitting one crafted round-1 commitment message — fully within the reach of an unprivileged-then-validator party using public inputs (the victim's published `EncryptionKeyMessage`). No collusion, no timing, and no invalid-curve tricks needed; `read`/`register` paths accept the bytes as-is.

### Recommendation
Bind `enc_key` to the participant registering it. Either include `enc_key` inside the signed `Commitments` payload so the existing PoK covers it, or add a Schnorr proof-of-possession over `enc_key` keyed to `(context, participant)` and verify it in `verify_r1`/`Decryption::register`. Additionally reject duplicate `enc_key` values across different `Participant` indexes and reject the identity point.

### Proof of Concept
1. Alice (participant `a`) publishes `EncryptionKeyMessage { msg: commitments_a, enc_key: K_a }` where `K_a = G * k_a`.
2. Mallory (participant `m`) publishes `EncryptionKeyMessage { msg: commitments_m, enc_key: K_a }` — copying `K_a`. `register` accepts it (`encryption.rs` L356-361); the PoK check passes because it only covers `commitments_m[0]` (`lib.rs` L323-329).
3. Every honest participant encrypts Mallory's share via `encrypt(..., enc_keys[&m] = K_a, share_m)` (`encryption.rs` L466, L153-154) and publishes it.
4. Alice reads each ciphertext, computes `ecdh(k_a, msg.key)` (`encryption.rs` L95-97, L487), decrypts each `SecretShare` evaluated at `m`, and sums them — recovering the full secret share for index `m` while Mallory herself cannot decrypt anything.