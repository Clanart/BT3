### Title
Attacker-controlled `Interpolation::Constant` coefficients in `ThresholdKeys::read` let untrusted serialized keys redefine the group key — (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes `t`, `n`, `i`, an interpolation type byte, `n` interpolation coefficients, a secret share, and `n` verification shares entirely from the input byte stream, then passes them to `ThresholdKeys::new`. `ThresholdKeys::new` validates only arity (`verification_shares.len() == n`, `t <= n`, `i <= n`, `t == n` for Constant) — it never checks that the secret share is consistent with the verification shares, nor that the constant coefficients correspond to any real key-generation transcript. Analogous to CVE-2024-32462 (where an attacker-controlled `commandline` was reinterpreted as `bwrap` options because no `--` separator enforced the data/option boundary), here the type byte and coefficient list let untrusted bytes smuggle in the *control parameters* of key interpolation, redefining which group key the deserialized `ThresholdKeys` claims to represent.

### Finding Description
`ThresholdKeys::read` reads a single type byte that selects `Interpolation::Constant` vs `Interpolation::Lagrange`. For Constant, it then reads `n` fully attacker-controlled scalars as the interpolation coefficients, an arbitrary `secret_share`, and `n` arbitrary `verification_shares`. [1](#0-0) 

`ThresholdKeys::new` computes the group key as `sum(verification_shares[i] * interpolation_factor(i))` over participants `1..=t` only, without checking `G * secret_share == verification_shares[i]` or any binding to a DKG session/context. [2](#0-1) 

`view()` applies `interpolation_factor(self.i, included)` to the secret share and to each verification share, so the attacker-chosen Constant coefficients directly scale every signature share produced from these keys. [3](#0-2) 

For `Interpolation::Constant`, `interpolation_factor` indexes `c[i - 1]` with no length check beyond the `n` read count, and no requirement the coefficients came from `musig`'s `binding_factor` derivation. [4](#0-3) 

### Impact Explanation
Any component that accepts serialized `ThresholdKeys` from an untrusted party (or from a store an attacker can write) receives a `ThresholdKeys` whose `group_key()` and signing behavior are fully attacker-defined. The attacker can pick coefficients and verification shares so that `group_key()` is a key they control outright (e.g., all coefficients zero except one, with verification share `G * k` for known `k`), while the structure claims arbitrary `(t, n, i)`. Concretely: funds addressed/scanned under `group_key()` are spendable by the attacker and not by the real set, and `view()` + signing produce shares under attacker-weighted interpolation, yielding signatures valid for the attacker's chosen key — an unintended-message / wrong-key signature and "funds reported received that are not spendable by the intended group" impact. Severity: High — it converts a deserialization routine into silent key substitution.

### Likelihood Explanation
Reachable only where `ThresholdKeys::read` consumes bytes an unprivileged party can supply or corrupt (e.g., keys transmitted between nodes or loaded from shared storage rather than locally generated via `key_gen`/`musig`/`PedPoP`, which construct `ThresholdKeys` internally and are safe). The primitive itself performs zero integrity or provenance checks — there is no MAC, session binding, or share/verification consistency check — so any such byte path is exploitable by crafting. If serialized keys only ever flow over authenticated, trusted channels, impact collapses, hence High rather than Critical.

### Recommendation
Treat the interpolation byte and coefficients as protected structure, not attacker data. When deserializing, verify `C::generator() * secret_share == verification_shares[i]` and, for `Interpolation::Constant`, verify the coefficients match the derivation that produced them (e.g., recompute `musig` binding factors from the committed key set and context, or reject Constant entirely in `read` unless the caller explicitly requests MuSig-shaped keys). Prefer authenticating serialized keys (MAC/signature under a setup key) so the type byte cannot be flipped.

### Proof of Concept
```rust
// Attacker crafts bytes for ThresholdKeys::<C>::read with n = t = 1:
//   ID len + ID | t=1 | n=1 | i=1 | type=0 (Constant) |
//   c[0] = F::ONE | secret_share = k (attacker-known) | share[1] = G*k
// ThresholdKeys::new accepts it; group_key() = G*k — a key the attacker
// fully controls, indistinguishable to callers from a real DKG key.
let mut buf = vec![];
buf.extend(4u32.to_le_bytes());  // wrong-len check is on C::ID; use real ID
buf.extend(C::ID);
buf.extend(1u16.to_le_bytes()); // t
buf.extend(1u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes()); // i
buf.push(0);                    // Interpolation::Constant
buf.extend(C::F::ONE.to_repr().as_ref());
buf.extend(attacker_scalar.to_repr().as_ref());          // secret_share = k
buf.extend((C::generator() * attacker_scalar).to_bytes().as_ref());
let keys = ThresholdKeys::<C>::read(&mut buf.as_slice()).unwrap();
assert_eq!(keys.group_key(), C::generator() * attacker_scalar);
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

**File:** crypto/dkg/src/lib.rs (L355-379)
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

```

**File:** crypto/dkg/src/lib.rs (L494-521)
```rust
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
