### Title
Quadratic-time Lagrange interpolation in `ThresholdKeys::read` enables CPU-exhaustion DoS from small untrusted input - (File: crypto/dkg/src/lib.rs)

### Summary
The `useragent` ReDoS advisory is an algorithmic-complexity bug: small attacker input triggers super-linear work in a parser. The analog in Serai is `ThresholdKeys::read` / `ThresholdKeys::new` in `crypto/dkg`. A serialized key blob declares `t` and `n` as `u16` fields controlled entirely by the input. With `Interpolation::Lagrange`, `ThresholdKeys::new` computes `interpolation_factor` for each of the `t` participants `1..=t`, and each call iterates over all `t` indexes — `O(t²)` field multiplications plus `t` field inversions. `t` can be as large as 65535, so a roughly 4 MB input forces ~4.3 billion field multiplications.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` from attacker-controlled bytes, then reads `n` group elements, and finally calls `ThresholdKeys::new` [1](#0-0) . `ThresholdKeys::new` computes the group key by summing `verification_shares[i] * interpolation_factor(i, &t)` over `t` participants [2](#0-1) . For `Interpolation::Lagrange`, `interpolation_factor` loops over every element of `included`, performing two field multiplications per element and a field inversion at the end [3](#0-2) . Total cost is `O(t²)` field multiplications and `O(t)` inversions, with `t` bounded only by `u16::MAX` — there is no cap relating `t`/`n` to message size or a sane protocol maximum.

The same quadratic behavior is reachable in `ThresholdKeys::view`, which calls `interpolation_factor` once per included signer (`O(|included|²)`), where `included` derives from the signing set [4](#0-3) .

### Impact Explanation
An unprivileged party who can cause a node/processor to deserialize attacker-influenced `ThresholdKeys` bytes (or a serialized multisig containing them) can pin a CPU core for an extended period with a ~4 MB input: 65535 × 65535 ≈ 4.3 × 10⁹ field multiplications plus 65535 inversions. This stalls the affected component (key loading, signing-session setup) — an availability failure, matching the Medium severity class of the reference advisory.

### Likelihood Explanation
Likelihood depends on whether untrusted bytes actually reach `ThresholdKeys::read` in a deployment (the rules treat this surface as in-scope). The input is trivially constructible: `u16` fields permit `t = n = 65535` with no cryptographic validity required beyond decodable `F`/`G` elements, and `ThresholdParams::new` only enforces `t <= n` [5](#0-4) . No secret, no valid shares, and no protocol role are needed to trigger the cost.

### Recommendation
Cap `n`/`t` at deserialization to a protocol-sane maximum (e.g., a few hundred), before performing interpolation. Alternatively or additionally, compute Lagrange coefficients in `O(t)` via the standard prefix/suffix product trick (product of all `(x_j)` terms, then per-index division), and bound `included.len()` early in `view`. Add a regression test asserting `ThresholdKeys::read` rejects oversized `n` quickly.

### Proof of Concept
```rust
// crypto/dkg — conceptual PoC (source-level argument; not executed)
use std::io::Cursor;
use ciphersuite::Secp256k1;
use frost::dkg::ThresholdKeys; // ThresholdKeys lives in dkg crate
use group::GroupEncoding;

let mut buf = vec![];
// curve ID len + ID
buf.extend((Secp256k1::ID.len() as u32).to_le_bytes());
buf.extend(Secp256k1::ID);
// t = n = 65535, i = 1
buf.extend(65535u16.to_le_bytes());
buf.extend(65535u16.to_le_bytes());
buf.extend(1u16.to_le_bytes());
buf.push(1); // Interpolation::Lagrange
// secret_share (any valid F repr)
buf.extend(<Secp256k1 as Ciphersuite>::F::ONE.to_repr());
// n verification shares (any valid G reprs, e.g. generator repeated)
let g = Secp256k1::generator().to_bytes();
for _ in 0 .. 65535 { buf.extend(g.as_ref()); }

// ~4 MB input -> ~4.3e9 field multiplications + 65535 inversions
let _keys = ThresholdKeys::<Secp256k1>::read(&mut Cursor::new(buf));
```

Root cause confirmed by code analysis: `t`/`n` are read as `u16` with only `t <= n` enforced [6](#0-5) , `ThresholdKeys::new` iterates `t` interpolation_factor calls [2](#0-1) , and each Lagrange `interpolation_factor` is `O(len(included))` [3](#0-2) .

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

**File:** crypto/dkg/src/lib.rs (L500-507)
```rust
    let mut verification_shares = HashMap::with_capacity(included.len());
    for i in &included {
      let verification_share = self.core.verification_shares[i];
      let verification_share = verification_share *
        self.scalar *
        self.core.interpolation.interpolation_factor(*i, &included);
      verification_shares.insert(*i, verification_share);
    }
```

**File:** crypto/dkg/src/lib.rs (L591-632)
```rust
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
