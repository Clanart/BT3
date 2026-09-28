### Title
`ThresholdKeys::read` accepts attacker-controlled bytes without binding `secret_share` to `verification_shares`, yielding keys whose reported `group_key` is inconsistent with the held share - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The external report's class — unsanitized user-supplied input producing state inconsistent with what callers assume — maps onto `ThresholdKeys::read` / `ThresholdKeys::new` in the DKG crate. The deserializer reads `t`, `n`, `i`, an interpolation method (including `n` arbitrary Constant-interpolation coefficients), a `secret_share`, and `n` verification shares from an untrusted reader, yet never verifies that `secret_share` is non-zero or that `C::generator() * secret_share == verification_shares[i]`. The `group_key` is then derived solely from `verification_shares[1..=t]`, completely independent of the deserialized `secret_share`.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs, lines 574–632) parses all fields from raw bytes and calls `ThresholdKeys::new` (lines 349–391). `ThresholdKeys::new` only checks:
- `verification_shares.len() == n` and all participant indexes `<= n` (lines 355–365),
- `t <= n`, `t != 0`, `n != 0`, `i <= n` via `ThresholdParams::new` (lines 166–179),
- `t == n` when `Interpolation::Constant` is used (lines 367–374).

It then computes `group_key = Σ verification_shares[i] * interpolation_factor(i, 1..=t)` over participants `1..=t` (lines 376–378) and stores the caller-supplied `secret_share` verbatim. There is no check that:

- `secret_share != 0`,
- `C::generator() * secret_share == verification_shares[params.i()]` (the share actually belongs to the participant index `i` encoded in the same bytes),
- with `Interpolation::Constant`, the `n` coefficients interpolate a polynomial whose evaluations at each participant index are consistent with the encoded verification shares.

A downstream consumer (`view`, `sign`, `group_key`) treats these fields as mutually consistent: `view()` scales `secret_share` by the interpolation factor and adds the offset to `included[0]` (lines 494–521), producing signature shares verified against `verification_shares`, while `group_key()` is derived from the unrelated `verification_shares` map.

### Impact Explanation
Any caller that feeds attacker-influenced bytes into `ThresholdKeys::read` ends up with a `ThresholdKeys` whose advertised `group_key` is fully attacker-controlled (choose any `verification_shares`, e.g. all `G * a` for a known `a`, making `group_key = G * a`) while the embedded `secret_share` is an unrelated scalar. This enables:

- **Funds reported received that are not spendable**: a node that loads/persists such keys and derives addresses or verifies incoming outputs against `group_key()` will attribute funds to a key the local share cannot sign under — signature shares produced by `view()`/`sign()` will fail verification against `verification_shares[i]`, so the validator set is stuck with an unspendable reported key.
- **Silent key substitution**: because `read` checks `C::ID` but nothing binds the share to the shares map, swapped-in bytes silently change the effective group key without any integrity error.

The consistency check is cheap (`G * secret_share == verification_shares[i]` plus, for `Constant` interpolation, `evaluate(i) * G == verification_shares[i]`), and its absence is precisely the "unsanitized input reaches state that other functions assume is consistent" bug class, realized on Serai's deserialization surface.

### Likelihood Explanation
Medium. `ThresholdKeys::read` is a public API explicitly designed for untrusted byte streams, and the rules admit untrusted bytes fed to `ThresholdKeys::read` as reachable input. Exploitation requires the attacker to influence the serialized key material a participant loads (backup import, vector/test import paths like `vectors_to_multisig_keys`, or any transport carrying `serialize()` output). No threshold collusion or malicious validator assumptions are needed — only delivery of crafted bytes to the `read` path. Impact is Medium: denial of signing for a substituted group key / unspendable attributed funds, not direct private-key recovery.

### Recommendation
In `ThresholdKeys::new` (or `read`), after the existing checks:

```rust
// crypto/dkg/src/lib.rs, inside ThresholdKeys::new
if bool::from(secret_share.is_zero()) {
  Err(DkgError::ZeroParameter { t: params.t(), n: params.n() })?; // or a dedicated error
}
if verification_shares[&params.i()] != C::generator() * secret_share.deref() {
  Err(DkgError::InvalidParticipant { n: params.n(), participant: params.i() })?;
}
// For Interpolation::Constant, additionally verify each encoded share:
//   for l in 1..=n { assert verification_shares[l] == G * evaluate(coeffs, l) }
```

This binds every deserialized field to every other, so `read` can only produce internally consistent keys.

### Proof of Concept
```rust
use dkg::{ThresholdKeys, ThresholdParams, Participant, Interpolation};
use ciphersuite::{Ciphersuite, group::Group};
use zeroize::Zeroizing;
use std::collections::HashMap;

// For any C: Ciphersuite
// Attacker-controlled group key: pick a scalar `a`, set all shares' points to G * a
let a = C::random_nonzero_F(&mut rng);
let evil_point = C::generator() * a;

let n = 3u16; let t = 2u16;
let mut verification_shares = HashMap::new();
for l in 1 ..= n {
  verification_shares.insert(Participant::new(l).unwrap(), evil_point);
}

// secret_share is an arbitrary, unrelated scalar — never checked
let unrelated = Zeroizing::new(C::random_nonzero_F(&mut rng));

let keys = ThresholdKeys::<C>::new(
  ThresholdParams::new(t, n, Participant::new(1).unwrap()).unwrap(),
  Interpolation::Lagrange,
  unrelated,                       // inconsistent with verification_shares[1] == G*a
  verification_shares,
).unwrap();                        // succeeds: no consistency check

// group_key() is Lagrange-interpolated from G*a points => attacker-known discrete log `a`,
// while the holder's `unrelated` share can never produce a valid signature share under it.
assert_eq!(keys.group_key(), C::generator() * a);
// G * secret_share != verification_shares[1], yet no error was raised.
```

Equivalently via the byte-level path: serialize any `ThresholdKeys`, then mutate the `secret_share` field in the byte stream (offset after `ID | t | n | i | interpolation`), and `ThresholdKeys::read` accepts it, producing a `group_key` the encoded share does not correspond to. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** crypto/dkg/src/lib.rs (L166-179)
```rust
  pub const fn new(t: u16, n: u16, i: Participant) -> Result<ThresholdParams, DkgError> {
    if (t == 0) || (n == 0) {
      return Err(DkgError::ZeroParameter { t, n });
    }

    if t > n {
      return Err(DkgError::InvalidThreshold { t, n });
    }
    if i.0 > n {
      return Err(DkgError::InvalidParticipant { n, participant: i });
    }

    Ok(ThresholdParams { t, n, i })
  }
```

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
