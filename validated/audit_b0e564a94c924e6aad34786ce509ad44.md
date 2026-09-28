### Title
PedPoG DKG: Identity encryption key accepted, making secret shares decryptable by any observer - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The bug class in CVE-2024-24550 — attacker-supplied input accepted without proper validation, turning a protected channel into an attacker-controlled one — maps onto Serai's PedPoP DKG encryption-key registration. `EncryptionKeyMessage::read` accepts an arbitrary group element as a participant's encryption public key via the ciphersuite-level `C::read_G`, which only enforces canonical encoding and does **not** reject the identity point (unlike FROST's `Curve::read_G`, which does). `Decryption::register` stores the key with no validity check. If a participant registers the identity point as their encryption key, every ECDH performed against it yields the identity point, so the ChaCha20 keystream is derived entirely from public data (the DKG `context` plus the constant encoding of identity) with a static IV. All `EncryptedMessage`s addressed to that participant are then decryptable by any passive observer of the protocol traffic — including the coordinator that relays the share blobs — recovering the participant's secret share `share_j = Σ f_i(j)` without any protocol fault being detected.

### Finding Description
Root cause chain, all in `crypto/dkg/pedpop/src/encryption.rs`:

1. `EncryptionKeyMessage::read` reads `enc_key` with `C::read_G(reader)` — the `Ciphersuite::read_G` implementation in `crypto/ciphersuite/src/lib.rs` (lines 91–101) checks canonical encoding only. The identity point encodes canonically (e.g., `0x01…00` for Ed25519, canonical for Ristretto), so it is accepted. The identity-rejecting variant `Curve::read_G` (`crypto/frost/src/curve/mod.rs` lines 125–131) is in a different trait and is not used here.

2. `Decryption::register` (encryption.rs lines 351–362) inserts `msg.enc_key` into `enc_keys` unconditionally — no identity, torsion, or proof-of-possession check on the encryption key itself. `SecretShareMachine::verify_r1` (`crypto/dkg/pedpop/src/lib.rs` lines 300–338) verifies the PoK signature on `commitments[0]` but never inspects `enc_key`.

3. `Encryption::encrypt` (encryption.rs lines 460–467) calls `encrypt(rng, context, i, enc_keys[participant], msg)`, which computes `ecdh = to * key` (line 96). With `to = identity`, `ecdh = identity` for every message.

4. `cipher` (encryption.rs lines 101–133) derives the ChaCha20 key solely from `context` and the ECDH point bytes, with the fixed IV `"DKG IV v0.2\0"`. Since `context` is public and `identity` has a fixed public encoding, the keystream is publicly computable.

Reachability: the malicious participant broadcasts their `EncryptionKeyMessage` in round 1 of PedPoP (relayed by the coordinator per `processor/src/key_gen.rs` lines 265–281). Every honest participant then encrypts their polynomial evaluation `f_i(j)` to identity. An unprivileged observer of the share messages (the relaying coordinator, or any network eavesdropper — the encryption layer exists precisely because the channel is not assumed confidential) recomputes `cipher(context, identity)` and XORs it against the ciphertext, recovering each `f_i(j)` and hence participant `j`'s combined secret share.

Notably, the PoP on `EncryptedMessage` does not prevent this: it proves possession for the *ephemeral* per-message key, not the registered long-term `enc_key`, which has no proof of possession at all.

### Impact Explanation
An unprivileged party with read access to DKG round-2 traffic recovers the threshold secret share of any participant who registered an identity encryption key. This is secret-key-share material leaking to parties who were never intended to see it: the PedPoP encryption exists specifically so that the share-relaying channel need not be confidential. The leaked share is a valid FROST key share; combined with shares obtained through any other means, it contributes directly to group key recovery. Additionally, because the keystream is public, the recipient `j` cannot distinguish "encrypted to me" from "broadcast to everyone," and any later blame evaluation (`decrypt_with_proof`, encryption.rs lines 366–397) also operates over publicly derivable keys.

### Likelihood Explanation
Requires one malicious DKG participant submitting a crafted round-1 message — a low barrier: the identity point is a canonical encoding, the PoK check in `verify_r1` is over the commitments (which the attacker generates honestly), and no code path inspects `enc_key`. The attack is fully deterministic and silently succeeds; no honest party can detect it, since encryption/decryption still "work" (the keystream is symmetric — `j` can decrypt their own shares using the public keystream). The residual limitation is that only messages addressed to the malicious participant leak; recovering the full group key still requires the share set to cross threshold.

### Recommendation
Reject the identity point (and ideally require a proof of possession) for registered encryption keys. Concretely: in `EncryptionKeyMessage::read` or `Decryption::register`, check `!bool::from(enc_key.is_identity())`, or switch to an identity-rejecting point read. Adding a Schnorr PoK binding `enc_key` to `(context, participant)` would additionally prevent registration of keys whose discrete log is unknown, closing related key-substitution issues.

### Proof of Concept
Conceptual, against `crypto/dkg/pedpop`:

1. Malicious participant `j` builds a round-1 `EncryptionKeyMessage<Commitments>` normally, then overwrites the trailing `enc_key` bytes with the canonical identity encoding (`[1, 0, …, 0]` for `Ed25519`/`Ristretto` in `dalek-ff-group`). The `Commitments` PoK verifies unchanged since `enc_key` is not bound into `cached_msg` or the challenge.

2. Honest participants' `SecretShareMachine::generate_secret_shares` accepts the message (`verify_r1` passes), and each computes `EncryptedMessage { key: r·G, msg: share_i ⊕ ChaCha20(key = H(context ‖ identity_bytes), iv = "DKG IV v0.2\0") }`.

3. A passive observer computes the identical keystream from `context` (public DKG session ID) and the known identity encoding, XORs each ciphertext, obtains `f_i(j)` for all `i`, and sums them to recover participant `j`'s secret share — verified by `Σ f_i(j)·G == verification_share_j` from the broadcast commitments.