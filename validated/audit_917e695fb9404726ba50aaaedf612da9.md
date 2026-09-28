### Title
`ThresholdKeys::read` accepts semantically inconsistent key sets — deserialized secret share is never checked against the verification shares, letting attacker-supplied bytes define an arbitrary group key - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` deserializes `t`, `n`, `i`, the interpolation method, the local `secret_share`, and `n` verification shares, then calls `ThresholdKeys::new`. Neither `read` nor `new` validates that `secret_share * G == verification_shares[i]`, that any verification share is non-identity, or that the verification shares are mutually consistent (e.g., lie on a degree `t-1` polynomial). The group key is derived purely from `verification_shares[1..=t]`: [1](#0-0) [2](#0-1) 

`Ciphersuite::read_G` only enforces canonical encoding — it explicitly permits the identity point (identity rejection lives in `Curve::read_G`, which `ThresholdKeys::read` does not use): [3](#0-2) [4](#0-3) 

### Finding Description
This is the direct analog of the MediaCMS class: insufficient validation of attacker-supplied structured input before it is committed into a security-critical object. The deserialization routine validates encoding-level properties (curve ID, canonical scalars/points, `Participant` index bounds via `ThresholdParams::new`, `t == n` for `Interpolation::Constant`, `participant <= n`) but never validates the *semantic* invariant of a threshold key: that the secret share and the verification shares describe the same polynomial and the same key.

Consequences of the missing checks:

1. **Attacker-chosen group key.** `group_key` is computed as `sum(verification_shares[i] * interpolation_factor(i, 1..=t))` with no proof of possession and no consistency check. An attacker who supplies `ThresholdKeys` bytes can select verification shares `G*x_j` for scalars `x_j` they know, making the resulting `group_key` a key whose discrete log is fully known to the attacker (Lagrange coefficients are public). The blob still deserializes successfully and `view()`, `scale()`, `offset()` all operate normally.

2. **Mismatched secret share.** `secret_share` is accepted verbatim. If it does not equal the interpolation of the verification shares at `i`, every `SignatureShare` this key produces is invalid for the stated `group_key`, yet this is only detected later — `complete()` falls through share validation into `FrostError::InternalError("everyone had a valid share yet the signature was still invalid")`, a code path the comments admit exists precisely for "deserializing a semantically invalid FrostKeys": [5](#0-4) 

3. **Identity verification shares.** Because `Ciphersuite::read_G` is used rather than `Curve::read_G`, identity points are accepted as verification shares, enabling shares that contribute nothing to the group key while still occupying a participant slot.

### Impact Explanation
Two concrete impact classes, both reachable through `ThresholdKeys::read` (an explicitly in-scope untrusted-bytes entry point):

- **Key/share substitution → attacker-known "threshold" key.** If a deserialized `ThresholdKeys` is used to derive a deposit/receive address (the normal role of `group_key` in Serai), an attacker who supplied or replaced the serialized blob obtains an address whose underlying key they fully control. Funds reported as received under this key are spendable *by the attacker*, not by the threshold — matching the accepted "funds reported received that are not spendable [by the group]" / key-substitution impact.
- **Denial of correct signing / forced inconsistency.** A mismatched `secret_share` yields views that pass all per-participant checks (`view()` validates indexes only) yet produce shares that never aggregate, and can also be steered so that a subset of verification shares are attacker-controlled, skewing `verification_share` checks during blame attribution.

### Likelihood Explanation
Reachability is bounded: `ThresholdKeys::read` is used wherever serialized key material is imported (backup/restore, key handoff between machines such as the recovery/promotion flows in `crypto/dkg`, or any persisted store an attacker can write). Wherever untrusted or mutable bytes reach this reader, the attack is deterministic — no probability assumptions, no collusion threshold required; the attacker simply chooses the verification shares. Where the bytes come exclusively from a fully trusted local store, impact reduces to latent inconsistency. The bug class (insufficient input validation on an ingest path) and the missing invariant check are unconditional facts of the code.

### Recommendation
In `ThresholdKeys::new` (or in `read` before construction):

1. Reject identity verification shares: `if bool::from(share.is_identity()) { Err(...) }` for every entry.
2. Verify the secret share matches: `assert_eq!(C::generator() * secret_share, verification_shares[&params.i()])` (returning an error, not a panic), since `C::read_F` already guarantees a canonical scalar.
3. Optionally verify the verification shares lie on a common degree `t-1` polynomial (check a few random evaluation points, or all `n` points via multi-exponentiation), closing the inconsistency gap entirely.
4. Consider using `Curve::read_G`-style identity rejection at the `dkg` layer, since `dkg` cannot depend on `frost::Curve`, a local `read_G`-with-identity-check helper is appropriate.

### Proof of Concept
Conceptual construction (no code execution available in this mode):

```rust
// Attacker picks a known scalar k and arbitrary share values
let k = Scalar::random(rng);          // attacker knows k
let n = 3u16; let t = 2u16;

// Craft verification shares so group_key = G*k:
// group_key = Σ_{i=1..=t} V_i * λ_i  → choose V_1 = G*k / λ_1, V_2 = identity
// (identity is accepted because Ciphersuite::read_G does not reject it)
let lam1 = lagrange::<C>(Participant(1), &[Participant(1), Participant(2)]);
let shares = [(1u16, C::generator() * k * lam1.invert().unwrap()),
              (2u16, C::G::identity()),
              (3u16, C::G::identity())];

// Serialize per ThresholdKeys::read layout:
//   u32 C::ID.len() || C::ID || t || n || i || interpolation=1 || secret_share || shares[1..=n]
// Result: ThresholdKeys::read succeeds; keys.group_key() == G * k
// Any address derived from group_key is fully controlled by the attacker,
// while params(), view(), and signing proceed without error.
```

Caveat: I was unable to trace the exact upstream call sites that feed attacker-controlled bytes into `ThresholdKeys::read` (e.g., processor key recovery/handoff paths) within the available search iterations, so the reachability claim rests on the sanctioned assumption that serialized `ThresholdKeys` are an untrusted-bytes surface per the stated scope rules.

### Citations

**File:** crypto/dkg/src/lib.rs (L347-391)
```rust
impl<C: Ciphersuite> ThresholdKeys<C> {
  /// Create a new set of ThresholdKeys.
  pub fn new(
    params: ThresholdParams,
    interpolation: Interpolation<C::F>,
    secret_share: Zeroizing<C::F>,
    verification_shares: HashMap<Participant, C::G>,
  ) -> Result<ThresholdKeys<C>, DkgError> {
    if verification_shares.len() != usize::from(params.n()) {
      Err(DkgError::IncorrectAmountOfVerificationShares {
        n: params.n(),
        shares: verification_shares.len(),
      })?;
    }
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }

    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }

    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();

    Ok(ThresholdKeys {
      core: Arc::new(Zeroizing::new(ThresholdCore {
        params,
        interpolation,
        secret_share,
        group_key,
        verification_shares,
      })),
      scalar: C::F::ONE,
      offset: C::F::ZERO,
    })
  }
```

**File:** crypto/dkg/src/lib.rs (L574-632)
```rust
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<ThresholdKeys<C>> {
    {
      let different = || io::Error::other("deserializing ThresholdKeys for another curve");

      let mut id_len = [0; 4];
      reader.read_exact(&mut id_len)?;
      if u32::try_from(C::ID.len()).unwrap().to_le_bytes() != id_len {
        Err(different())?;
      }

      let mut id = vec![0; C::ID.len()];
      reader.read_exact(&mut id)?;
      if id != C::ID {
        Err(different())?;
      }
    }

    let (t, n, i) = {
      let mut read_u16 = || -> io::Result<u16> {
        let mut value = [0; 2];
        reader.read_exact(&mut value)?;
        Ok(u16::from_le_bytes(value))
      };
      (
        read_u16()?,
        read_u16()?,
        Participant::new(read_u16()?).ok_or(io::Error::other("invalid participant index"))?,
      )
    };

    let mut interpolation = [0];
    reader.read_exact(&mut interpolation)?;
    let interpolation = match interpolation[0] {
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
      1 => Interpolation::Lagrange,
      _ => Err(io::Error::other("invalid interpolation method"))?,
    };

    let secret_share = Zeroizing::new(C::read_F(reader)?);

    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
  }
```

**File:** crypto/ciphersuite/src/lib.rs (L91-101)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
  }
```

**File:** crypto/frost/src/curve/mod.rs (L125-131)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```

**File:** crypto/frost/src/sign.rs (L447-495)
```rust
  fn complete(
    self,
    mut shares: HashMap<Participant, SignatureShare<C>>,
  ) -> Result<A::Signature, FrostError> {
    let params = self.params.multisig_params();
    validate_map(&shares, self.view.included(), params.i())?;

    let mut responses = HashMap::new();
    responses.insert(params.i(), self.share);
    let mut sum = self.share;
    for (l, share) in shares.drain() {
      responses.insert(l, share.0);
      sum += share.0;
    }

    // Perform signature validation instead of individual share validation
    // For the success route, which should be much more frequent, this should be faster
    // It also acts as an integrity check of this library's signing function
    if let Some(sig) = self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum) {
      return Ok(sig);
    }

    // We could remove blame_entropy by taking in an RNG here
    // Considering we don't need any RNG for a valid signature, and we only use the RNG here for
    // performance reasons, it doesn't feel worthwhile to include as an argument to every
    // implementor of the trait
    let mut rng = ChaCha20Rng::from_seed(self.blame_entropy);
    let mut batch = BatchVerifier::new(self.view.included().len());
    for l in self.view.included() {
      if let Ok(statements) = self.params.algorithm.verify_share(
        self.view.verification_share(*l),
        &self.B.bound(*l),
        responses[l],
      ) {
        batch.queue(&mut rng, *l, statements);
      } else {
        Err(FrostError::InvalidShare(*l))?;
      }
    }

    if let Err(l) = batch.verify_vartime_with_vartime_blame() {
      Err(FrostError::InvalidShare(l))?;
    }

    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
  }
```
