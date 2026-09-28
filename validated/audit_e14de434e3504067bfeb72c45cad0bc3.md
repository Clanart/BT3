### Title
`ThresholdKeys::read` accepts identity verification shares, enabling a group key with known discrete log — (File: crypto/dkg/src/lib.rs)

### Summary
The audit hint is a missing zero-value check on a trusted-input path. In Serai, the analogous shape is `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg`: serialized verification shares are read with `C::read_G` — the ciphersuite-level reader which only enforces canonical encodings, not non-identity — and `ThresholdKeys::new` validates share count and participant indexes but never checks that any verification share is non-identity. FROST's own `Curve::read_G` does reject identity (`crypto/frost/src/curve/mod.rs:125-131`), but `ThresholdKeys::read` deliberately uses the non-FROST `Ciphersuite::read_G`, bypassing that check.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` reads `n` verification shares with `<C as Ciphersuite>::read_G(reader)?` (line 622), which accepts the canonically-encoded identity point. `ThresholdKeys::new` (lines 349-391) then computes the group key by interpolating the verification shares of participants `1..=t`:

```rust
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

If an attacker supplies serialized `ThresholdKeys` bytes where the shares for participants `1..=t` are the identity encoding, `ThresholdKeys::new` succeeds and yields a `ThresholdKeys` whose `group_key()` is the identity — a public key whose discrete logarithm (0) is publicly known. For interpolation methods where the factors don't sum to a coefficient that clears the identity contributions (Constant interpolation: factor 1 each; Lagrange evaluated at the group's base), the identity shares propagate identity into the group key.

### Impact Explanation
A `ThresholdKeys`/`ThresholdView` with identity `group_key` produces signatures against a public key with known discrete log 0. Schnorr/FROST verification `s·G == R + c·Y` with `Y = identity` reduces to `s·G == R`, so anyone can forge a valid signature for any message (choose any `R = s·G`; `c` multiplies identity away). Any funds or authorization bound to that group key are spendable by an unprivileged party — equivalent to a full key recovery/forgery, not merely a misconfiguration.

### Likelihood Explanation
Requires the victim node to deserialize attacker-influenced `ThresholdKeys` bytes (e.g., keys material restored/exchanged via an untrusted channel rather than produced by the local DKG). This is a narrower trust path than a network message, but `ThresholdKeys::read` is explicitly an untrusted-bytes entry point, and the code contains no defense-in-depth check (contrast `Curve::read_G`, which rejects identity precisely because identity keys are catastrophic). Severity: Medium — conditional on the deserialization trust path, but impact is total key compromise when reached.

### Recommendation
In `ThresholdKeys::new` (or in `ThresholdKeys::read`), reject any identity verification share:

```rust
for share in verification_shares.values() {
  if bool::from(share.is_identity()) {
    Err(DkgError::InapplicableInterpolation("identity verification share"))?;
  }
}
```

Optionally also reject an identity computed `group_key`. This mirrors the existing identity rejection in `Curve::read_G`.

### Proof of Concept
Serialize a `ThresholdKeys` blob for e.g. `t = n = 2`, `i = 1`, `Interpolation::Constant([ONE, ONE])`, arbitrary non-zero `secret_share`, and set both verification share encodings to the identity encoding (32 zero bytes for Ristretto). `ThresholdKeys::read` accepts it; `group_key()` returns `C::G::identity()`. A Schnorr signature `(R = s·G, s)` for arbitrary `s` then verifies against that group key, demonstrating forgery.