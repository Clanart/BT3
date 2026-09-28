### Title
PedPoP accepts identity/small-order ECDH points in `read`, letting a malicious participant force all shares addressed to them under a publicly known key - (File: crypto/dkg/pedpop/src/encryption.rs)

### Summary
The upstream report's bug class is "a generic input routine accepts attacker-controlled values in a form the check was not meant to permit (`is_file` satisfied by `phar://`/`ftp://` wrappers), silently converting a safe-looking operation into an attacker-chosen side effect." Serai's analogue lives in PedPoP's deserialization: `EncryptedMessage::read`, `EncryptionKeyMessage::read`, and `Commitments::read` all parse group elements with `Ciphersuite::read_G`, which only enforces canonical encoding. The rest of Serai's FROST stack deliberately wraps this with `Curve::read_G`, which additionally rejects the identity point (`crypto/frost/src/curve/mod.rs:125-131`). PedPoP's message readers never apply that check, so a DKG participant can supply the identity point (and, for cofactored groups such as dalek-ff-group's `Ed25519`/`EdwardsPoint`, small-order/torsion points) as their encryption key or per-message ephemeral key.

### Finding Description
`EncryptionKeyMessage::read` parses the sender's encryption key with `C::read_G` (the `Ciphersuite` method), and `EncryptedMessage::read` does the same for the per-message `key` field:

- `crypto/dkg/pedpop/src/encryption.rs:57-59`: `Ok(Self { msg: M::read(reader, params)?, enc_key: C::read_G(reader)? })`
- `crypto/dkg/pedpop/src/encryption.rs:171-176`: `key: C::read_G(reader)?, pop: SchnorrSignature::<C>::read(reader)?, ...`
- `crypto/dkg/pedpop/src/lib.rs:115-125`: `Commitments::read` likewise reads commitment points via `C::read_G`.

`Ciphersuite::read_G` (`crypto/ciphersuite/src/lib.rs:91-100`) checks only `from_bytes` + canonical re-encoding; it does not reject identity. `Curve::read_G` exists precisely to add that rejection for FROST inputs, but PedPoP doesn't use it.

Two consequences:

1. **ECDH to the identity point.** `ecdh` is `public * private` (`encryption.rs:95-97`), and `cipher` derives the ChaCha20 key solely from `transcript(context || ecdh_bytes)` with a fixed IV (`encryption.rs:101-133`). If a participant registers `enc_key = identity`, then every honest party computing `encrypt(rng, context, from, to = identity, share)` derives the shared point `key * identity = identity`, i.e. a constant, publicly known cipher key. Every `SecretShare` addressed to that participant is then encrypted under a key any observer of the authenticated channel can recompute.
2. **Trivially satisfiable PoP for the identity key.** `SchnorrSignature::verify` checks `s*G == R + c*A`. With `key = A = identity`, `c*A = identity`, so any `(R = r*G, s = r)` passes — the proof-of-possession designed to stop key reuse/forgery (`encryption.rs:83-91`) is vacuous for the identity key. For cofactored curves, a small-order `key` or `enc_key` confines the ECDH result to the cofactor subgroup (8 possibilities for Ed25519), making the stream key brute-forceable.

The PoK signature on `commitments[0]` is also verifiable with `A = identity` (`lib.rs:323-329` batches it like any other signature), so a participant can register commitments whose first element is identity without any discrete-log proof doing real work.

### Impact Explanation
An unprivileged DKG participant broadcasts an `EncryptionKeyMessage` whose `enc_key` is the identity (or a small-order) point, with fully valid serialization and a trivially valid PoP. All honest participants' `SecretShare`s for that participant are then encrypted under `cipher(context, identity)` — a publicly computable ChaCha20 key — so every share contributed *to* participant `l` leaks to anyone who can read the shares channel. Since a participant's final secret share is `sum_j f_j(l)` (`lib.rs:484`), leaking all `f_j(l)` reveals `l`'s complete threshold secret share `s_l`, enabling share recovery — an in-scope impact — and in combination with compromised/cooperating peers can contribute to key-share reconstruction. The identity commitment additionally lets a participant skip a meaningful PoK over their constant term, weakening the rogue-key defense the proof is meant to provide.

### Likelihood Explanation
The attack requires only broadcasting a malformed-but-canonical `EncryptionKeyMessage` (or `EncryptedMessage`) during a PedPoP key generation — bytes fully within an unprivileged participant's control and explicitly reachable via `EncryptedMessage::read`/`EncryptionKeyMessage::read`/`Commitments::read` (`processor/src/key_gen.rs:404-412` shows these are parsed straight from coordinator-supplied share blobs). No cryptographic break is needed; the missing check is a single missing `is_identity` test. Confirmed shares remain individually verified, so exploiting the leak into a full key compromise additionally requires enough colluding/compromised shares — fitting Medium rather than High.

### Recommendation
In `crypto/dkg/pedpop/src/encryption.rs` and `crypto/dkg/pedpop/src/lib.rs`, replace `Ciphersuite::read_G` usage for `enc_key`, `EncryptedMessage.key`, and commitment points with an identity-rejecting read (e.g. apply `is_identity` rejection as `Curve::read_G` does), and additionally reject non-prime-order points for cofactored groups (e.g. verify `point * cofactor`-cleared or require `PrimeGroup` subgroup membership / multiply-by-cofactor check). Optionally hard-fail `EncryptedMessage::read`/`EncryptionKeyMessage::read` when the parsed point is identity so such messages are dropped before ECDH.

### Proof of Concept
```rust
// Attacker participant l registers an encryption key of the identity point.
use ciphersuite::group::GroupEncoding;
use dkg_pedpop::{EncryptionKeyMessage, Commitments, PedPoP};
use frost::curve::Ed25519; // any C: Ciphersuite

// Build EncryptionKeyMessage bytes: valid Commitments payload || identity enc_key.
let mut msg = honest_commitments.serialize();           // attacker-controlled commitments
msg.extend(Ed25519::G::identity().to_bytes().as_ref()); // identity point, canonical encoding

let ekm = EncryptionKeyMessage::<Ed25519, Commitments<Ed25519>>::read(
    &mut msg.as_slice(),
    params,
).unwrap(); // SUCCEEDS: Ciphersuite::read_G does not reject identity

// Every honest participant i then calls:
//   encrypt(rng, ctx, i, to = ekm.enc_key, share_i_to_l)
// with ecdh = key_i * identity = identity, so their ChaCha20 key is
//   transcript(ctx || identity.to_bytes()) — publicly derivable.
// Any observer decrypts share_i_to_l; collecting all j gives sum_j f_j(l) = s_l.
```
Per-message variant: send `EncryptedMessage { key: identity, pop: (R = r*G, s = r), msg }`; the PoP verifies (`s*G = R = R + c*identity`) and the ciphertext decrypts under the public `cipher(context, identity)` key.