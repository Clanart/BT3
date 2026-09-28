### Title
Insufficient validation in `ThresholdKeys::read`/`ThresholdKeys::new` allows deserializing semantically inconsistent threshold keys, producing a group key whose signatures can never be produced - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` parses a curve ID, `(t, n, i)`, an interpolation variant, a `secret_share` scalar, and `n` verification shares, then hands them to `ThresholdKeys::new`. Neither function performs any semantic validation: the secret share is never checked against `verification_shares[i]` (`generator() * secret_share == verification_shares[i]`), verification shares are not checked to lie on any common polynomial, and identity points are accepted (plain `Ciphersuite::read_G`, which only enforces canonical encoding). `group_key` is then derived purely as the interpolation of `verification_shares[1..=t]`, completely independent of the supplied secret share.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `secret_share` via `C::read_F` (line 618) and each verification share via `C::read_G` (lines 620-623), then calls `ThresholdKeys::new` (lines 625-631). Inside `ThresholdKeys::new` (lines 349-391), the only checks are:

- `verification_shares.len() == n` and participant indexes `<= n` (lines 355-365)
- `Constant` interpolation requires `t == n` (lines 367-374)

It then computes `group_key` as the interpolated sum over participants `1..=t` (lines 376-378) without ever verifying:

1. `C::generator() * secret_share == verification_shares[i]` — the deserialized secret share is never bound to the verification share at this participant's index.
2. That the verification shares interpolate consistently (a share for participant `j` other than `1..=t` can be arbitrary garbage and still produce a "valid" group key).
3. Non-identity of group elements — `Ciphersuite::read_G` (crypto/ciphersuite/src/lib.rs:91-101) only checks canonical encoding, unlike `Curve::read_G` in FROST which rejects identity (crypto/frost/src/curve/mod.rs:125-131).

The downstream impact of this missing check is acknowledged by the codebase itself: `AlgorithmSignatureMachine::complete` comments that the "only known way" to reach its `InternalError` state — where every share verifies yet the aggregate signature is invalid — "is to deserialize a semantically invalid FrostKeys" (crypto/frost/src/sign.rs:491-494). Additionally, `ThresholdView::verification_share` (crypto/dkg/src/lib.rs:680-682) indexes `self.verification_shares[&l]`, which is built only for `included` participants; inconsistent share data shifts failures to panics/blame paths rather than a clean deserialization error.

### Impact Explanation
The group key returned by `group_key()` is computed exclusively from the attacker-supplied verification shares (crypto/dkg/src/lib.rs:376-378, 445-447). A malformed blob therefore produces a key handle that reports a normal-looking group key — which is what address/scanner derivation consumes (e.g., `p2tr_script_buf` over the group key in networks/bitcoin/src/wallet/mod.rs:80-86, 162-165) — while the embedded `secret_share` does not satisfy `secret_share * G == verification_shares[i]`. Every FROST signing attempt with these keys produces a share that fails `verify_share` against the interpolated verification share, so `complete` can never emit a valid signature (crypto/frost/src/sign.rs:475-494). Funds deposited to the reported group key/address are received but are not spendable — exactly the class of failure the schema recognizes. Alternatively, an attacker who can supply the blob can set `verification_shares` so the reported group key differs from the key the other honest participants hold (each derives `group_key` from their own stored shares), splitting the view of the multisig's receive address.

### Likelihood Explanation
Reachability requires attacker-influenced bytes to reach `ThresholdKeys::read` — e.g., a key blob loaded from storage or transmitted through an untrusted recovery/coordination channel, both plausible given `read` is a public deserialization API on the untrusted-bytes surface. Crafting the blob is trivial: take a valid `serialize()` output and flip bytes in the `secret_share` field (still a canonical scalar), or substitute verification shares with other canonical points. `ThresholdKeys::read` accepts it, `group_key()` reports normally, and the failure only surfaces at signing time. Medium severity: integrity/availability-of-funds impact gated behind delivery of the blob to the key-loading path.

### Recommendation
In `ThresholdKeys::new` (and thus `ThresholdKeys::read`), add:

- A consistency check: `C::generator() * secret_share == verification_shares[&params.i()]`, rejecting otherwise.
- Optionally reject identity verification shares (use an identity-rejecting read or `is_identity` check) since an identity share can never correspond to a nonzero secret share under a functioning DKG.
- Document that `read` validates share consistency, or add a `ThresholdKeys::verify_integrity()` that callers must invoke after deserialization.

### Proof of Concept
```rust
// Construct or obtain a valid serialized ThresholdKeys blob for some curve C.
let mut bytes = keys.serialize().to_vec();

// Layout: id_len(4) || id || t(2) || n(2) || i(2) || interp(1 [+ scalars]) ||
//         secret_share(F::Repr) || n * verification_share(G::Repr)
// Flip a byte inside the secret_share field (keeping it canonical), e.g.:
let secret_share_offset = bytes.len()
    - (F_REPR_LEN + (n as usize) * G_REPR_LEN); // start of secret_share
bytes[secret_share_offset] ^= 1; // still canonical for low-order changes

// Deserialization succeeds — no check binds secret_share to verification_shares[i].
let keys = ThresholdKeys::<C>::read(&mut bytes.as_slice()).unwrap();

// group_key() is unchanged (it is derived solely from verification_shares[1..=t]).
assert_eq!(keys.group_key(), original_group_key);

// But generator() * secret_share != verification_shares[i]:
// every sign() -> complete() now fails share verification, so no valid signature
// can ever be produced for the reported group key. Any funds sent to the
// address derived from keys.group_key() are received yet unspendable.
```