### Title
Attacker-controlled `n`/`t` in `ThresholdKeys::read` trigger quadratic-cost Lagrange interpolation and per-participant inversions (CPU DoS) - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::<C>::read` accepts a serialized key blob whose `t` and `n` fields are attacker-controlled `u16`s. It then calls `ThresholdKeys::new`, which computes the group key by evaluating a Lagrange `interpolation_factor` for each of `1..=t` participants, where each factor evaluation loops over all `t` included indexes and performs a field inversion. A payload of only ~`32·n` bytes therefore induces `O(t²)` field multiplications plus `t` field inversions — a large amplification of CPU work per input byte, analogous to CVE-2025-6203's "small valid payload, disproportionate computation" class.

### Finding Description
In `ThresholdKeys::read`, `t`, `n`, and `i` are read directly from the byte stream (`crypto/dkg/src/lib.rs:591-602`). For `Interpolation::Constant` it pre-allocates `Vec::with_capacity(n)` and reads `n` scalars (lines 604-616), and it reads `n` group elements as verification shares (lines 620-623). These reads are linear in the input, but the subsequent `ThresholdKeys::new` call (lines 625-632) is not: it builds `t = 1..=params.t()` and computes `group_key` as `sum(verification_shares[i] * interpolation_factor(*i, &t))` (lines 376-378). `interpolation_factor` for `Interpolation::Lagrange` iterates over the whole `included` set and calls `denom.invert().unwrap()` per participant (lines 229-247).

So an attacker supplying `n = t = 65535` with ~2 MB of arbitrary point bytes forces ~4.3×10⁹ field multiplications and 65,535 field inversions inside a single `read` call — before `ThresholdParams::new` or any other cheap check can reject anything (the payload is, in fact, structurally valid). Since `read` is the documented deserialization entry point for untrusted key material, an unprivileged party who can feed bytes to it can stall the host for orders of magnitude longer than the input size would suggest.

### Impact Explanation
A relatively small (~2 MB) attacker-controlled input causes billions of scalar multiplications and tens of thousands of field inversions on the processing node, blocking the thread handling the deserialization. Repeated submissions can keep validators/processors saturated, delaying or preventing DKG completion, signing, or other coordinator duties — a denial of service proportional to attacker bandwidth but greatly amplified in CPU cost. This mirrors the Vault advisory: payload within size limits, yet excessive CPU/memory consumption.

### Likelihood Explanation
The path requires only that an attacker reach code that calls `ThresholdKeys::read` (or any caller that reconstructs keys from serialized bytes received over the wire / from peer-supplied data). No key material, threshold coalition, or internal state is needed — the cost is incurred purely by `ThresholdKeys::new` during deserialization, before any validity check could cheaply reject the input. The expensive work occurs even if the shares are garbage, because `read_G` accepts any canonically-encoded point.

### Recommendation
- Bound `t`/`n` to a sane protocol maximum (e.g., the validator-set limit) before reading `n` group elements or running interpolation — reject early in `read` rather than relying on `ThresholdParams::new` after the work is done.
- Compute the group key with a linear-time method (prefix products / batch inversion) or lazily, instead of `t` separate `O(t)` Lagrange evaluations each with its own inversion.
- For `Interpolation::Constant`, avoid `Vec::with_capacity(n)` before validating `n` against the actual remaining input length.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs — attacker-controlled bytes:
//   id_len || C::ID || t=0xFFFF || n=0xFFFF || i=1 || interp=1 (Lagrange)
//   || secret_share || 65535 encoded G points
//
// ThresholdKeys::read (line 574):
//   - reads n=65535 verification shares (~2 MB total input)
//   - calls ThresholdKeys::new (line 625)
//
// ThresholdKeys::new (line 376-378):
//   group_key = sum over i in 1..=65535 of
//     verification_shares[i] * interpolation_factor(i, &t)
//
// interpolation_factor (line 229-247):
//   per i: loops all 65535 included participants, then denom.invert()
//   => ~4.3e9 field muls + 65535 inversions from a ~2 MB payload
``` [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** crypto/dkg/src/lib.rs (L376-391)
```rust
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
