### Title
Missing validation of critical key material in `ThresholdKeys::read`/`ThresholdKeys::new` permits zero interpolation factors and inconsistent shares, yielding an identity group key - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to an admin setter that accepts `address(0)`, `ThresholdKeys::read` deserializes the `Interpolation::Constant` coefficient vector, the `secret_share`, and every `verification_share` straight from attacker-supplied bytes and hands them to `ThresholdKeys::new`, which validates only counts and participant indexes — never that interpolation factors are non-zero, that the group key is non-identity, or that `secret_share` is consistent with `verification_shares[i]`. A crafted blob can therefore install a "zero" (identity) group key or a share set that silently no-ops verification, and the signing code itself acknowledges this hole (`crypto/frost/src/sign.rs:491-494`).

### Finding Description
`ThresholdKeys::read` reads `n` field elements as `Interpolation::Constant` coefficients via `C::read_F`, a `secret_share` via `C::read_F`, and `n` verification shares via `C::read_G`, then calls `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` checks only `verification_shares.len() == n` and `participant <= n`; it computes `group_key` as the sum of `verification_shares[i] * interpolation_factor(i, 1..=t)` for `i in 1..=t` and stores everything without checking non-identity or share consistency [2](#0-1) .

For `Interpolation::Constant`, `interpolation_factor` is just `c[i-1]` [3](#0-2) . Setting all `t` coefficients used by participants `1..=t` to `F::ZERO` makes `group_key = G::identity()` — the cryptographic equivalent of `address(0)`. Any Schnorr signature `(R, s)` with `R = s·G` verifies against an identity public key because `verify` checks `R + cA − sG = 0` and the `cA` term vanishes [4](#0-3) .

Additionally, nothing binds `secret_share` to `verification_shares[params.i()]`; `view()` happily interpolates whatever secret was supplied [5](#0-4) . The FROST completion path explicitly notes an inconsistent deserialized key set is reachable: "The only known way to cause this ... is to deserialize a semantically invalid FrostKeys" [6](#0-5) .

### Impact Explanation
An unprivileged party who can feed crafted bytes to `ThresholdKeys::read` (a listed reachable deserialization target) obtains `ThresholdKeys` whose `group_key()` is the point at infinity. Any downstream verifier (FROST `Algorithm::verify` → `SchnorrSignature::verify`) that trusts this group key accepts universally forgeable signatures — for every message, `s·G = R` satisfies `R + c·∞ − s·G = 0`. Alternatively, a mismatched `secret_share`/`verification_shares` pair makes the holder sign shares that cannot blame correctly, breaking the protocol like a zeroed `positionsManager`.

### Likelihood Explanation
Exploitation requires an attacker to control the serialized `ThresholdKeys` bytes consumed by a node/verifier (backup import, key-transfer, or any path exposing `ThresholdKeys::read` to untrusted input). That is a narrower reach than a public on-chain call — the honest DKG paths (`key_gen`, `musig`, `promote`) always produce consistent, non-zero material — hence Medium rather than High.

### Recommendation
In `ThresholdKeys::new` (or `read`), reject semantically invalid material:
- `require(group_key != G::identity())`;
- for `Interpolation::Constant`, reject any zero coefficient for participants `1..=t`;
- verify `C::generator() * secret_share == verification_shares[params.i()]` (a one-multiplication consistency check that also catches corrupted shares);
- reject identity verification shares for `1..=t`.

### Proof of Concept
```rust
// Crafted serialization with t=n=2, i=1, Constant interpolation [0, 0]
let mut serialized = vec![];
serialized.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
serialized.extend(C::ID);
serialized.extend(2u16.to_le_bytes()); // t
serialized.extend(2u16.to_le_bytes()); // n
serialized.extend(1u16.to_le_bytes()); // i
serialized.push(0);                    // Interpolation::Constant
serialized.extend(C::F::ZERO.to_repr().as_ref()); // c[0] = 0
serialized.extend(C::F::ZERO.to_repr().as_ref()); // c[1] = 0
serialized.extend(C::F::random(&mut rng).to_repr().as_ref()); // arbitrary secret_share
// arbitrary verification shares (e.g., honest points); participants 1..=t
// are multiplied by zero factors, so their values don't matter
for _ in 0 .. 2 {
  serialized.extend((C::generator() * C::F::random(&mut rng)).to_bytes().as_ref());
}

let keys = ThresholdKeys::<C>::read::<&[u8]>(&mut serialized.as_ref()).unwrap();
assert!(bool::from(keys.group_key().is_identity())); // "address(0)" group key

// Forgery against the identity group key: any (R = s*G, s) verifies
let s = C::F::random(&mut rng);
let sig = SchnorrSignature::<C> { R: C::generator() * s, s };
assert!(sig.verify(keys.group_key(), H::hram(&sig.R, &keys.group_key(), b"any message")));
```

### Citations

**File:** crypto/dkg/src/lib.rs (L226-248)
```rust
  fn interpolation_factor(&self, i: Participant, included: &[Participant]) -> F {
    match self {
      Interpolation::Constant(c) => c[usize::from(u16::from(i) - 1)],
      Interpolation::Lagrange => {
        let i_f = F::from(u64::from(u16::from(i)));

        let mut num = F::ONE;
        let mut denom = F::ONE;
        for l in included {
          if i == *l {
            continue;
          }

          let share = F::from(u64::from(u16::from(*l)));
          num *= share;
          denom *= share - i_f;
        }

        // Safe as this will only be 0 if we're part of the above loop
        // (which we have an if case to avoid)
        num * denom.invert().unwrap()
      }
    }
```

**File:** crypto/dkg/src/lib.rs (L355-391)
```rust
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

**File:** crypto/dkg/src/lib.rs (L493-521)
```rust
    // The interpolation occurs multiplicatively, letting us scale by the scalar now
    let secret_share_scaled = Zeroizing::new(self.scalar * self.original_secret_share().deref());
    let mut secret_share = Zeroizing::new(
      self.core.interpolation.interpolation_factor(self.params().i(), &included) *
        secret_share_scaled.deref(),
    );

    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }

    /*
      The offset is included by adding it to the participant with the lowest ID.

      This is done after interpolating to ensure, regardless of the method of interpolation, that
      the method of interpolation does not scale the offset. For Lagrange interpolation, we could
      add the offset to every key share before interpolating, yet for Constant interpolation, we
      _have_ to add it as we do here (which also works even when we intend to perform Lagrange
      interpolation).
    */
    if included[0] == self.params().i() {
      *secret_share += self.offset;
    }
    *verification_shares.get_mut(&included[0]).unwrap() += C::generator() * self.offset;
```

**File:** crypto/dkg/src/lib.rs (L604-631)
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
```

**File:** crypto/schnorr/src/lib.rs (L88-110)
```rust
  pub fn batch_statements(&self, public_key: C::G, challenge: C::F) -> [(C::F, C::G); 3] {
    // s = r + ca
    // sG == R + cA
    // R + cA - sG == 0
    [
      // R
      (C::F::ONE, self.R),
      // cA
      (challenge, public_key),
      // -sG
      (-self.s, C::generator()),
    ]
  }

  /// Verify a Schnorr signature for the given key with the specified challenge.
  ///
  /// This challenge must be properly crafted, which means being binding to the public key, nonce,
  /// and any message. Failure to do so will let a malicious adversary to forge signatures for
  /// different keys/messages.
  #[must_use]
  pub fn verify(&self, public_key: C::G, challenge: C::F) -> bool {
    multiexp_vartime(&self.batch_statements(public_key, challenge)).is_identity().into()
  }
```

**File:** crypto/frost/src/sign.rs (L491-494)
```rust
    // If everyone has a valid share, and there were enough participants, this should've worked
    // The only known way to cause this, for valid parameters/algorithms, is to deserialize a
    // semantically invalid FrostKeys
    Err(FrostError::InternalError("everyone had a valid share yet the signature was still invalid"))
```
