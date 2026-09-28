### Title
`ThresholdKeys::read` accepts degenerate/inconsistent serialized key material — no marker distinguishes a well-formed key from a zero-initialized or corrupted one - (File: crypto/dkg/src/lib.rs)

### Summary
The upstream bug is that nothing in an event ring file indicates whether it was fully initialized, so consumers can consume a partially-written or zero-truncated file as if it were valid. Serai exhibits the same bug class in `ThresholdKeys::read`: the serialized key format has no integrity marker binding `secret_share` to `verification_shares`, the per-party verification shares are read with `Ciphersuite::read_G` (which does *not* reject the identity point, unlike `Curve::read_G` used for FROST nonces), and `ThresholdKeys::new` never checks that `secret_share * G == verification_shares[i]` nor that the reconstructed `group_key` is non-identity. A truncated/zero-filled or attacker-crafted key file therefore deserializes into a "valid" `ThresholdKeys` whose group key is attacker-known or identity.

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, `i`, the interpolation coefficients, the secret share, and `n` verification shares, then hands them to `ThresholdKeys::new` (crypto/dkg/src/lib.rs:574-632). Two gaps mirror the uninitialized-file flaw:

1. **Identity verification shares are accepted.** Shares are read via `<C as Ciphersuite>::read_G` (crypto/dkg/src/lib.rs:620-623), which only checks canonicality, not identity (crypto/ciphersuite/src/lib.rs:91-101). The FROST layer explicitly rejects identity points in `Curve::read_G` (crypto/frost/src/curve/mod.rs:123-131), but `ThresholdKeys` deserialization bypasses that wrapper entirely.

2. **No consistency/initialization check.** `ThresholdKeys::new` computes `group_key` by interpolating verification shares `1..=t` (crypto/dkg/src/lib.rs:376-378) and stores `secret_share` verbatim, without verifying `secret_share * G == verification_shares[i]` or `!group_key.is_identity()`. A zero-truncated file (identity shares, zero share, `t=1,n=1`) is accepted and yields `group_key = identity` — a key whose discrete log is publicly known (0). The `complete()` error path even acknowledges this hole: "The only known way to cause this, for valid parameters/algorithms, is to deserialize a semantically invalid FrostKeys" (crypto/frost/src/sign.rs:491-494).

### Impact Explanation
An attacker who can feed crafted bytes to `ThresholdKeys::read` (e.g., a supplied/recovered key file, or corrupted storage read as a "truncated-to-zero" file — exactly the residual case the upstream fix still permits) obtains a `ThresholdKeys` object that passes all validation while representing a degenerate key. With `verification_shares[1] = identity`, `secret_share = 0`, `t = n = 1`, `Interpolation::Lagrange`, the resulting `group_key` is the identity point. Any Bitcoin output or protocol state keyed to that group key is spendable/forgeable by anyone, since Schnorr verification reduces to `sG = R` with a known private key of zero. More generally, any inconsistency between `secret_share` and `verification_shares[i]` is undetected, so the holder signs shares that either fail aggregation (DoS-to-blame) or authorize under an attacker-controlled group key — funds are received at an address the operator cannot exclusively spend. Medium: it requires the victim to deserialize attacker-influenced key material rather than purely on-the-wire signing input.

### Likelihood Explanation
Requires an attacker to supply or corrupt the serialized `ThresholdKeys` blob the victim loads — analogous to the upstream scenario where a consumer mmaps a file before initialization completes. Within the threat model (untrusted bytes fed to `ThresholdKeys::read`), the deserialization path is fully attacker-controlled and every check it performs is structural only; the probability of acceptance is 1 for the degenerate encoding.

### Recommendation
In `ThresholdKeys::new` (or `read`), reject identity verification shares and reject an identity `group_key` (use the same identity check `Curve::read_G` applies). Additionally verify `C::generator() * secret_share == verification_shares[&i]` so a partial/inconsistent serialization is rejected, mirroring the "marker indicating full initialization" fix upstream.

### Proof of Concept
```
// Attacker constructs bytes for ThresholdKeys::<Secp256k1>::read:
//   id_len = len(C::ID) as u32 LE; id = C::ID
//   t = 1u16 LE, n = 1u16 LE, i = 1u16 LE
//   interpolation = 0x01 (Lagrange)
//   secret_share = 32 zero bytes (valid canonical scalar 0)
//   verification_shares[1] = 33-byte encoding of the identity/infinity point
//     (canonical, so Ciphersuite::read_G accepts it)
// Result: ThresholdKeys::new succeeds; group_key() == identity.
// Any Schnorr/FROST signature under this group key is forgeable by anyone,
// and any Bitcoin output to the corresponding address is spendable by the attacker.
```