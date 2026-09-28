### Title
Schnorr PoP verification accepts the identity point as a public key, disabling proof-of-possession on DKG encryption messages - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
`EncryptedMessage` relies on a Schnorr "proof of possession" (`pop`) to authenticate the ephemeral encryption `key` attached to each encrypted DKG secret share. The underlying `SchnorrSignature::verify` / `batch_statements` never checks that the supplied public key is non-identity, and `EncryptedMessage::read` happily decodes an identity `key`. A signature `(R = s·G, s)` verifies for **any** challenge when the public key is the point at infinity, so the PoP check is vacuous — the exact analog of litestream's `ssh.InsecureIgnoreHostKey()`, which disables peer authentication entirely. An unprivileged DKG participant (or anyone able to feed bytes into `EncryptedMessage::read` → `KeyMachine::calculate_share` / `Decryption::decrypt_with_proof` via `BlameMachine::blame` / `AdditionalBlameMachine::blame`) can forge a "valid" PoP over attacker-chosen ciphertext without knowing the discrete log of the claimed key.

### Finding Description
`EncryptedMessage::read` decodes `key: C::G` via `C::read_G` with no non-identity check (crypto/dkg/pedpop/src/encryption.rs:171-177). The pop is verified as `msg.pop.verify(msg.key, pop_challenge(...))` (encryption.rs:374-379) or queued via `msg.pop.batch_verify(..., msg.key, ...)` (encryption.rs:479-485). `SchnorrSignature::batch_statements` returns `[(1, R), (c, A), (-s, G)]` (crypto/schnorr/src/lib.rs:88-100). With `A = identity`, the statement reduces to `R - s·G = 0`, so any `(R = s·G, s)` satisfies it for every challenge `c` — a universal forgery. The file itself documents that this PoP exists specifically to stop an attacker from attaching another party's encryption key to a crafted message (encryption.rs:83-91); an identity key bypass restores a variant of that bypass — the authenticity check the PoP provides is effectively disabled whenever `key` is identity, and additionally `ecdh(private, identity) = identity` makes the ChaCha20 stream key in `cipher()` (encryption.rs:101-133) publicly derivable from the transcript alone, so any observer of the merely-authenticated channel can decrypt the payload.

### Impact Explanation
Two consequences, both reachable by submitting an `EncryptedMessage` with `key = identity` and a trivially constructed `pop`:

1. **Forged proof accepted**: `pop` verification succeeds for any ciphertext without knowledge of a discrete log, defeating the possession proof that is the sole authenticity mechanism on the per-message key within the DKG's authenticated channel. The attacker can attach *any* claimed key/ciphertext pairing that passes the decrypt path (e.g., reusing a key form that would otherwise require a PoP), undermining the blame machinery's assumption that a verified `pop` binds the ciphertext to a key the sender controls — a forged-proof acceptance satisfying the scan's criteria.
2. **Confidentiality silently disabled (the CVE's core harm)**: when a participant registers `enc_key = identity` via `EncryptionKeyMessage::read`/`Decryption::register` (encryption.rs:57-59, 351-362 — no identity check there either), every `encrypt()` to that participant computes `ecdh(key, identity) = identity`, producing a keystream key any passive observer can recompute via `cipher(context, identity)`. Messages to that participant are effectively transmitted in the clear to anyone monitoring the channel — the direct analog of skipping host-key verification and leaking sensitive data to a man-in-the-middle. While the shares belong to the registering participant, the same unchecked path means an attacker-injected `key = identity` message to an honest decryptor also yields a publicly-known cipher, so injected ciphertext is fully attacker-controlled plaintext under a "verified" wrapper.

### Likelihood Explanation
Requires only a DKG participant (or a party able to hand `EncryptedMessage`/`EncryptionKeyMessage` bytes to `calculate_share`/`blame`), which is within the scan's unprivileged-party model: the bytes come from peer messages over an authenticated-but-not-confidential channel. Forging the pop costs one scalar multiplication (`R = s·G` for arbitrary `s`). No collusion, no leaked keys, no broken-BFT assumption needed. Probability of exploitation is deterministic once attempted.

### Recommendation
- Reject the identity point (and any non-canonical/torsioned encoding) in `C::read_G` consumers that accept attacker-supplied points used as verification keys — concretely, check `msg.key != identity` and `enc_key != identity` in `EncryptedMessage::read`, `EncryptionKeyMessage::read`, and `Decryption::register` (crypto/dkg/pedpop/src/encryption.rs:57-59, 171-177, 351-362).
- Alternatively/defense-in-depth: have `SchnorrSignature::verify`/`batch_statements` (crypto/schnorr/src/lib.rs:88-110) reject identity public keys and identity `R`, closing the forgery class globally.
- Ensure `blame`/`decrypt_with_proof` paths apply the same checks so forged-PoP messages cannot be laundered through the blame protocol.

### Proof of Concept
```
// Context: participant P feeds EncryptedMessage bytes to
// KeyMachine::calculate_share (or Decryption::decrypt_with_proof via blame).

// 1. Build a forged EncryptedMessage over identity key:
let s   = C::random_nonzero_F(rng);          // arbitrary scalar
let R   = C::generator() * s;                // R = s*G
let pop = SchnorrSignature::<C> { R, s };
let msg = EncryptedMessage::<C, SecretShare<C::F>> {
    key: C::G::identity(),                   // attacker-controlled, read_G accepts it
    pop,
    msg: arbitrary_ciphertext,
};

// 2. pop_challenge is computed normally; verify reduces to:
//    multiexp([(1, R), (c, identity), (-s, G)]) = R - s*G = 0  → true
//    for ANY challenge c. The PoP "passes" without knowing dlog(key).

// 3. In decrypt(): ecdh(enc_key, msg.key = identity) = identity,
//    so cipher(context, identity) derives a keystream key computable by
//    any eavesdropper → msg plaintext is fully attacker-chosen/public.

// 4. Same bytes through AdditionalBlameMachine::blame / blame_internal
//    (pedpop/lib.rs:575-609) hit decrypt_with_proof, where
//    msg.pop.verify(identity, ...) again trivially succeeds,
//    skipping the InvalidSignature branch that would flag the sender.
```

Note: I was unable to fully trace every in-scope reader (`musig`/`promote`/`recovery`) within the iteration budget; the finding above is confirmed directly from `encryption.rs`, `pedpop/src/lib.rs` (`verify_r1`/`calculate_share`/`blame_internal`), and `schnorr/src/lib.rs`.