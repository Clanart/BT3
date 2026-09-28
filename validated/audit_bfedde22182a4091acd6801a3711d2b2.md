### Title
`ThresholdKeys::new`/`ThresholdKeys::read` never validate that the secret share matches the verification shares, allowing a crafted key blob to silently substitute the group key - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::new` accepts a `secret_share` scalar and a map of `verification_shares` and derives `group_key` purely by interpolating `verification_shares[1..=t]`. It never checks that `C::generator() * secret_share == verification_shares[params.i()]`, nor that any verification share is non-identity (`read` uses `Ciphersuite::read_G`, which — unlike `Curve::read_G` — does not reject identity points). This is the same class as the Astaria finding: the constructor/deserializer stores several interdependent values without validating they are mutually consistent, so shuffled or attacker-chosen values are silently accepted.

### Finding Description
`ThresholdKeys::read` deserializes `(t, n, i)`, an interpolation method (including a `Constant` vector of `n` attacker-supplied scalars), a `secret_share`, and `n` verification-share points, then calls `ThresholdKeys::new`. The only validations in `new` are:

- `verification_shares.len() == n`
- no participant index exceeds `n`
- `Constant` interpolation only when `t == n` [1](#0-0) 

`group_key` is then computed as `sum(verification_shares[i] * interpolation_factor(i, [1..=t]))` for `i in 1..=t`, with zero checks tying it to the secret share: [2](#0-1) 

The deserialization path feeds attacker-controlled bytes straight through: [3](#0-2) 

Because `Ciphersuite::read_G` only enforces canonicality (identity is allowed for the generic `dkg` crate; only `frost::Curve::read_G` rejects it), every verification share may be an arbitrary point: [4](#0-3) [5](#0-4) 

An attacker who supplies the byte stream to `ThresholdKeys::read` (a documented untrusted-input surface) can set verification shares to `G * a_j` for known scalars `a_j`, making `group_key` a key whose discrete log the attacker knows. FROST signing then proceeds normally — `view()` interpolates the (consistent) shares and produces valid signatures under the attacker's group key, since share verification in `verify_share` only checks shares against the same attacker-supplied verification shares.

### Impact Explanation
A node that loads such a key set will sign valid FROST signatures under an attacker-controlled group key and will treat outputs addressed to that group key as belonging to the multisig. Funds "received" at that address are spendable only by the attacker — the analog of Astaria's "deposits made to the wrong contract", i.e., funds reported received that are not spendable by the honest participants. Even without full key substitution, a `secret_share` inconsistent with `verification_shares[i]` makes the participant emit shares that fail `verify_share`, causing repeated blamed signing sessions.

### Likelihood Explanation
The attack requires an attacker to control the serialized `ThresholdKeys` bytes loaded by a participant. That is a stronger assumption than a pure on-chain input, but `ThresholdKeys::read` is exactly the kind of untrusted-byte entry point enumerated for this exercise, and the missing consistency check is a genuine gap: the constructor validates each field's shape but not their mutual correspondence, which is the root cause of the original finding.

### Recommendation
In `ThresholdKeys::new`, verify `C::generator() * secret_share == verification_shares[&params.i()]`. Additionally, reject identity verification shares in `dkg`'s deserialization (use the identity-rejecting read or check `is_identity`), and consider checking that `group_key` interpolation is consistent with the shares under a known-published group key where available.

### Proof of Concept
1. Attacker picks scalars `a_1..a_n`, computes `verification_shares[l] = G * a_l`, picks `secret_share = a_i`, chooses `t, n, i` and `Interpolation::Lagrange`.
2. Attacker serializes this into the `ThresholdKeys` wire format (curve ID, `t`, `n`, `i`, tag `1`, `a_i` repr, the `n` points) and has the victim call `ThresholdKeys::<C>::read` on it. `read` returns `Ok`.
3. `group_key()` equals a point whose discrete log `sum(a_l * l_l)` the attacker knows.
4. The victim's `AlgorithmSignMachine` signs messages under this group key; signatures verify for the attacker's key. Bitcoin outputs sent to the address derived from `group_key` are credited as received but are spendable only by the attacker.

### Citations

**File:** crypto/dkg/src/lib.rs (L349-391)
```rust
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

**File:** crypto/dkg/src/lib.rs (L604-632)
```rust
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

**File:** crypto/frost/src/curve/mod.rs (L124-131)
```rust
  #[allow(non_snake_case)]
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```
