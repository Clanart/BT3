### Title
Schnorr proof-of-possession accepts identity public key with trivially forged signature — missing identity check in `EncryptedMessage::read` / `SchnorrSignature::verify` - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
Analogous to a `setOwner(0)` that silently hands control to an unusable/unbound value, the PedPoP DKG's encryption layer accepts the identity (zero) point as a sender's per-message encryption key and proof-of-possession public key. Because `C::read_G` (the `Ciphersuite` trait version, not `frost::Curve::read_G`) performs canonical-encoding checks but **no identity rejection**, an unprivileged participant can submit an `EncryptedMessage`/`EncryptionKeyMessage` whose `key`/`enc_key` is the identity point. The Schnorr verification formula `R + c·A − s·G == 0` is then satisfiable without any discrete log (`R = identity`, `s = 0`), producing a forged PoP that defeats the explicit anti-rogue-key protection the PoP exists for.

### Finding Description
`EncryptionKeyMessage::read` (crypto/dkg/pedpop/src/encryption.rs:57-59) and `EncryptedMessage::read` (encryption.rs:171-177) both deserialize attacker-controlled points via `C::read_G`. `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) only enforces canonical encoding — unlike `frost::Curve::read_G` (crypto/frost/src/curve/mod.rs:125-131) it does **not** reject `is_identity`.

The identity point then feeds two places:

1. `Decryption::register` stores `msg.enc_key` unchecked (encryption.rs:351-362); `encrypt` computes `ecdh = to * key` (encryption.rs:95-97, 154). With `to = identity`, the ECDH shared point is always identity regardless of the ephemeral secret — the ChaCha20 key in `cipher` (encryption.rs:101-133) is publicly derivable, so shares "encrypted" to that participant are decryptable by anyone holding the ciphertext.
2. The PoP check `msg.pop.verify(msg.key, pop_challenge(...))` (encryption.rs:374-377, and the batch path at 479-485) calls `SchnorrSignature::verify`, whose `batch_statements` are `[R, c·A, −s·G]` (crypto/schnorr/src/lib.rs:88-110). With `A = msg.key = identity`, `R = identity`, `s = 0`, the multiexp equals identity for **any** challenge — a universal forgery.

The comment at encryption.rs:84-90 states the PoP specifically prevents an Eve-style attack where an attacker reuses another party's encryption key to mount a blame-proof ECDH-key leak. An identity key bypasses this entirely: the attacker needs no secret at all to produce a "valid" PoP, and the resulting ECDH shared key (`private * identity = identity`) collapses to a constant, so `cipher` keys become deterministic and publicly computable.

### Impact Explanation
- Any `EncryptedMessage` with `key = identity` carries a trivially forgeable PoP, defeating the proof-of-possession defense-in-depth and allowing attacker-crafted ciphertext/key pairings that the code explicitly assumes require knowledge of the discrete log.
- Any participant registering `enc_key = identity` causes shares sent to them to be encrypted under the publicly-known ECDH point `identity`, so the ChaCha20 key is derivable by any observer of the (broadcast/transcribed) ciphertext — key-share material addressed to that participant is recoverable by third parties, and blame proofs based on "revealing the ECDH key" are meaningless since the key is already public.
- Because `ThresholdKeys::read` (crypto/dkg/src/lib.rs:620-622) also uses `C::read_G` without identity checks, deserialized `verification_shares` may contain the identity point, yielding a degenerate `group_key` and shares that verify against identity — the "contract controlled by zero address" analog.

### Likelihood Explanation
Fully attacker-controlled bytes are accepted: `EncryptedMessage::read`, `EncryptionKeyMessage::read`, `SchnorrSignature::read`, and `ThresholdKeys::read` are all reachable from untrusted peer input in the DKG flow (`calculate_share` consumes `EncryptedMessage`s per participant, crypto/dkg/pedpop/src/lib.rs:463-492). Constructing the forgery requires zero work: encode the canonical identity point and `s = 0`. Exploitability is limited by the surrounding protocol's authenticated channel, but the crypto layer itself performs no defense — Medium.

### Recommendation
- Reject identity in `EncryptionKeyMessage::read` / `EncryptedMessage::read` / `Decryption::register` (use `frost::Curve::read_G`-style identity rejection, or add `is_identity` checks at registration).
- Reject identity `R` or identity public keys in `SchnorrSignature::verify`/`batch_statements` call sites handling untrusted keys (the tributary `Signed::read` already does this for `R`; crypto/schnorr does not).
- Reject identity `verification_shares` in `ThresholdKeys::new`/`ThresholdKeys::read`, and reject zero `secret_share`s.

### Proof of Concept
```rust
// crypto/schnorr: forged PoP for identity key, any challenge
use ciphersuite::{group::ff::Field, group::Group, Ciphersuite};
use schnorr::SchnorrSignature;

fn forged_pop<C: Ciphersuite>(challenge: C::F) -> bool {
    // Attacker supplies key = identity (zero address analog),
    // R = identity, s = 0 via SchnorrSignature::read on untrusted bytes
    let key: C::G = C::G::identity();
    SchnorrSignature::<C> { R: C::G::identity(), s: C::F::ZERO }
        .verify(key, challenge) // R + c*A - s*G = id + c*id - 0 = id -> true
}

// crypto/dkg/pedpop: registering an identity encryption key makes
// every share sent to this participant encrypted under ECDH = identity:
//   ecdh(enc_key, msg.key) with enc_key/peer point = identity -> identity
//   cipher(context, identity) -> publicly derivable ChaCha20 keystream
// EncryptionKeyMessage::read accepts it because Ciphersuite::read_G
// (crypto/ciphersuite/src/lib.rs:91-101) never calls is_identity.
```

Supporting citations: `Ciphersuite::read_G` lacks identity rejection at crypto/ciphersuite/src/lib.rs:91-101; `SchnorrSignature::verify`/`batch_statements` formula at crypto/schnorr/src/lib.rs:88-110; `EncryptedMessage::read` / `Decryption::register` / `ecdh`/`cipher` at crypto/dkg/pedpop/src/encryption.rs:95-133, 171-177, 351-362; the contrasting identity-rejecting `Curve::read_G` at crypto/frost/src/curve/mod.rs:125-131.