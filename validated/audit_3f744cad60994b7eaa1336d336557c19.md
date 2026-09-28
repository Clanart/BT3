### Title
Missing identity ("zero") check on verification shares lets `ThresholdKeys` deserialize an identity group key, enabling trivial signature forgery - ([File: crypto/dkg/src/lib.rs])

### Summary
The external report describes a missing zero-value check: an address setter accepts `0x0`, which then silently poisons every downstream operation. Serai has the same bug class at the threshold-key layer. `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349) and `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574) accept identity group elements as verification shares because they are decoded with `Ciphersuite::read_G`, which — unlike `frost::Curve::read_G` — only enforces canonicality and explicitly permits the identity element (crypto/ciphersuite/src/lib.rs:91-101). With identity verification shares for participants `1..=t`, the derived `group_key` becomes the group identity, and no code path rejects this. The result is a `ThresholdKeys` whose public key is the identity point, for which Schnorr/FROST verification is trivially forgeable.

### Finding Description
`Ciphersuite::read_G` validates canonicality but returns identity points happily:

```rust
// crypto/ciphersuite/src/lib.rs:91-101
fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    ...
    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)   // identity is returned here
}
```

FROST's `Curve::read_G` adds the missing check (`crypto/frost/src/curve/mod.rs:125-131`), but `ThresholdKeys::read` calls `<C as Ciphersuite>::read_G` directly for each verification share (`crypto/dkg/src/lib.rs:620-623`) and passes them to `ThresholdKeys::new`, which computes

```rust
// crypto/dkg/src/lib.rs:376-378
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

with no check that any `verification_shares[i]` or the resulting `group_key` is non-identity. For `Interpolation::Constant`, the interpolation factors are themselves attacker-controlled bytes read by `C::read_F` (dkg/src/lib.rs:607-612), so even arbitrary non-identity shares can be collapsed to an identity group key by choosing constant factors summing appropriately (e.g. all-zero factors are rejected by canonicality? No — `C::F::ZERO` is a perfectly canonical scalar, so `Interpolation::Constant(vec![0; n])` makes every term `0 * V_i = identity`, forcing `group_key = identity` for *any* verification shares).

The same identity-accepting `C::read_G` is used by `pedpop::Commitments::read` (crypto/dkg/pedpop/src/lib.rs:115-124), so a DKG participant can broadcast an all-identity coefficient commitment vector; the Schnorr PoK over `commitments[0]` is satisfiable for the identity public key with `s = r`, `R = r·G` (any `r`), since `R + c·identity − sG = 0` for every challenge `c` (schnorr/src/lib.rs:88-109).

### Impact Explanation
A `ThresholdKeys` whose `group_key()` is the identity makes Schnorr verification vacuous: `verify` checks `R + c·A − sG == identity`; with `A = identity` any `(R = sG, s)` passes for arbitrary `s`, regardless of `c`. That is a forged signature against the group's public key. More broadly, an identity verification share for participant `j` in a deserialized key set means `verify_share` for `j` reduces to `z_j·G == bound_nonce_j`, letting `j` produce "valid" signature shares without holding any real share of the group secret — signing of messages the honest share value would not authorize, since the share-consistency assumption (`V_j = λ_j·s_j·G`) has been silently voided. This matches the source bug exactly: a "zero" value accepted where a nonzero one was required corrupts all downstream operations that assumed validity.

### Likelihood Explanation
Reachability requires feeding attacker-influenced bytes to `ThresholdKeys::read`/`ThresholdKeys::new` or a malicious-but-single DKG participant sending identity commitments (permitted by the prompt's threat model for `Commitments::read`/`ThresholdKeys::read`). In the PedPoP path, a lone malicious participant can force the identity commitment vector, which zeroes their contribution to every party's secret share — their share of everyone else's key is known (zero), degrading the DKG's guarantee that each party contributes unpredictable entropy. Severity is Medium: exploitation needs control over serialized key material or a DKG session slot, but the code performs no defense at all where the analogous FROST path (`Curve::read_G`) explicitly rejects identity.

### Recommendation
- In `ThresholdKeys::new`, reject identity verification shares and an identity computed `group_key` (`Err` if `bool::from(v.is_identity())` for any share and for the sum).
- In `ThresholdKeys::read`, use an identity-rejecting point read (mirroring `frost::Curve::read_G`) for verification shares.
- In `pedpop::Commitments::read`/`verify_r1`, reject identity commitment points (`commitments[0]` at minimum) so that a participant cannot publish the zero polynomial.
- Reject `Interpolation::Constant` vectors that produce an identity group key, or at minimum reject all-zero coefficient vectors.

### Proof of Concept
Conceptual, against `ThresholdKeys::read` (dkg/src/lib.rs:574-632):

1. Serialize a blob for ciphersuite `C` (e.g. Ristretto): correct `C::ID`, `t = n` (e.g. `t = n = 2`), `i = 1`, interpolation byte `0` (Constant) followed by `n` canonical zero scalars, a canonical `secret_share`, and `n` identity point encodings (for Ristretto, the 32-byte encoding of the identity).
2. `ThresholdKeys::read` succeeds: `read_F` accepts zero scalars, `C::read_G` accepts identity points, `ThresholdParams::new` accepts `t = n`, and `ThresholdKeys::new` computes `group_key = Σ 0·identity = identity` without error.
3. `keys.group_key()` is now `C::G::identity()`. Any downstream Schnorr/FROST verification keyed by this group key accepts `(R = s·G, s)` for attacker-chosen `s` because `SchnorrSignature::batch_statements` yields `R + c·identity − sG = 0`.

For the PedPoP variant, a malicious participant broadcasts `params.t()` identity points as `Commitments` plus a Schnorr signature `(r·G, r)`; `verify_r1`'s batch PoK check passes since `public_key = identity` makes `r·G + c·identity − r·G = 0` for all `c`, and every subsequent share of `0` verifies against the all-identity commitments.