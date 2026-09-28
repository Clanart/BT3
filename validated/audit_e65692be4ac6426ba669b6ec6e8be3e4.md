### Title
`ThresholdKeys::read` deserializes an attacker-controlled participant count and `ThresholdKeys::new` performs quadratic Lagrange interpolation over it — unbounded CPU exhaustion from a ~4 MB input - (File: crypto/dkg/src/lib.rs)

### Summary
The bug class of the reference advisory (CWE-400/CWE-770 — a function trusted to bound work/resources that does not) maps onto `ThresholdKeys::<C>::read` in `crypto/dkg`. The serialized format embeds a `u16` participant count `n` that the deserializer trusts verbatim: it reads `n` scalars / `n` group elements and then calls `ThresholdKeys::new`, which derives the group key by Lagrange-interpolating `t` verification shares — an `O(t²)` field-multiplication + `t` inversions computation. With `t = n = 65535`, a single ~4 MB input forces ~4×10⁹ field multiplications inside deserialization, before any semantic check can reject the input.

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, `i` directly from the byte stream, then reads `n` `C::F` scalars (Constant interpolation) or `n` `C::G` points (verification shares) purely on the strength of the declared count [1](#0-0) . There is no cap on `n` other than the `u16` width of the field.

`ThresholdKeys::new` then computes the group key as `Σ_{i=1..=t} verification_shares[i] * interpolation_factor(i, t)` [2](#0-1) . For `Interpolation::Lagrange`, each `interpolation_factor` call iterates over the full `included` set (`t` elements) and performs one field inversion [3](#0-2) . Total cost is `Θ(t²)` multiplications plus `t` inversions — quadratic work driven entirely by an untrusted length field, exactly the "limit trusted but not enforced" shape: `Participant`/`ThresholdParams` validation only bounds `n ≤ u16::MAX` and `t ≤ n`, it does not bound the work [4](#0-3) .

Note `n = u16::MAX` is handled in `all_participant_indexes`, so `n = 65535` is a fully "valid" parameter set, not a rejected edge case [5](#0-4) .

### Impact Explanation
Any caller that feeds untrusted or semi-trusted bytes into `ThresholdKeys::read` (the format is the canonical serialization for `ThresholdKeys`/`FrostKeys` across the codebase) can stall the deserializing thread for billions of scalar multiplications plus ~65k field inversions per input. In a validator/processor context this is a CPU-exhaustion DoS of the signing/key-management path — precisely the resource-exhaustion consequence class of the reference advisory (concurrency limit silently not applied → unbounded work per request), transposed to Serai's count-driven deserialization. Recovery of a validator stuck interpolating 65,535 shares is effectively impossible within protocol timeouts, so threshold operations (and hence fund movement depending on the stalled party) halt.

### Likelihood Explanation
Requires the attacker to supply ~`(n * F_len) + (n * G_len)` bytes (~4 MB for Ristretto/Secp256k1-sized encodings at `n = 65535`) that reach a `ThresholdKeys::read` call site. Whether such a call site is exposed to unauthenticated peers depends on the integrator; the library itself places no size or count guard. Where reachable, exploitation is deterministic and repeatable.

### Recommendation
Enforce an explicit sanity bound on `t`/`n` inside `ThresholdKeys::read` before allocating and interpolating (e.g., reject `n` above a protocol-meaningful maximum such as a few thousand), and/or compute the group key with a linear-time approach: build the `O(t)` Lagrange numerator product once and derive each factor in `O(1)` additional work (standard "product of `(x - x_j)`" trick) instead of `t` independent `O(t)` interpolations. At minimum, move the `ThresholdParams::new` semantic check before the point/scalar reads so malformed `t > n` inputs are rejected prior to interpolation work.

### Proof of Concept
Conceptual, against `crypto/dkg` with any `Ciphersuite` (e.g., `dalek_ff_group::Ristretto`):

```rust
use dkg::ThresholdKeys;
use dalek_ff_group::Ristretto;

// Build a serialized ThresholdKeys claiming t = n = 65535, i = 1, Lagrange.
let n: u16 = u16::MAX;
let mut buf = vec![];
buf.extend(u32::try_from(<Ristretto as Ciphersuite>::ID.len()).unwrap().to_le_bytes());
buf.extend(<Ristretto as Ciphersuite>::ID);
buf.extend(n.to_le_bytes()); // t
buf.extend(n.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes()); // i
buf.push(1); // Interpolation::Lagrange
buf.extend([0u8; 32]); // secret_share (zero is still a canonical F)
for _ in 0 .. n {
    buf.extend(Ristretto::generator().to_bytes().as_ref()); // valid verification shares
}

// ~4 MB input -> ThresholdKeys::new performs 65535 Lagrange interpolation
// factors, each O(65535) muls + an inversion: ~4x10^9 field muls.
let _ = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice());
```

The read succeeds syntactically; the CPU cost is incurred inside `ThresholdKeys::new` at `crypto/dkg/src/lib.rs:376-378` via `Interpolation::Lagrange::interpolation_factor` (`crypto/dkg/src/lib.rs:229-247`).

### Citations

**File:** crypto/dkg/src/lib.rs (L145-162)
```rust
impl Iterator for AllParticipantIndexes {
  type Item = Participant;
  fn next(&mut self) -> Option<Participant> {
    if self.i > self.n {
      None?;
    }
    let res = Participant::new(self.i).unwrap();

    // If i == n == u16::MAX, we cause `i > n` by setting `n` to `0` so the iterator becomes empty
    if self.i == u16::MAX {
      self.n = 0;
    } else {
      self.i += 1;
    }

    Some(res)
  }
}
```

**File:** crypto/dkg/src/lib.rs (L164-179)
```rust
impl ThresholdParams {
  /// Create a new set of parameters.
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

**File:** crypto/dkg/src/lib.rs (L591-623)
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
```
