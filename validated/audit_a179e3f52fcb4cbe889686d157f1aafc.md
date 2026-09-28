### Title
Unbounded participant count makes `ThresholdKeys::read` quadratic and causes resource exhaustion - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` accepts a serialized participant count of up to 65,535, reads that many verification shares, and then computes the group key with `ThresholdKeys::new`. For each of the `t` included participants, `Interpolation::interpolation_factor` iterates over all `t` participants, producing `O(t²)` field arithmetic. An attacker-controlled serialized key can therefore cause billions of scalar operations before deserialization returns.

### Finding Description
`ThresholdKeys::read` obtains `t`, `n`, and `i` directly from the supplied byte stream and later reads `n` serialized verification shares. [1](#0-0)  `ThresholdParams::new` only rejects zero parameters, `t > n`, and `i > n`; it does not impose a practical maximum participant count. [2](#0-1)  `ThresholdKeys::new` then evaluates `interpolation_factor` once for every participant from `1..=t`. [3](#0-2)  For Lagrange interpolation, each evaluation iterates over the complete included participant list. [4](#0-3) 

With `t = n = 65,535`, a syntactically valid serialized key causes approximately `65,535²`, or over 4.2 billion, scalar operations during `ThresholdKeys::new`.

### Impact Explanation
An attacker who can provide bytes to `ThresholdKeys::read` can cause a caller to remain inside deserialization for a prohibitively large computation after reading only a few megabytes of input. This can exhaust CPU resources and prevent the caller from processing further protocol messages.

### Likelihood Explanation
The input is self-contained and does not require private key material, malformed curve encodings, or invalid participant indexes. The serialized points and scalars need only be canonical; repeated generator encodings are sufficient for reaching the expensive group-key calculation. The practical limitation is that the target must accept attacker-supplied serialized `ThresholdKeys`.

### Recommendation
Enforce a protocol-level maximum `n`/`t` before allocating or reading participant-dependent data. In addition, avoid the quadratic group-key reconstruction path by using an efficient aggregate Lagrange evaluation or another sub-quadratic reconstruction algorithm. Reject serialized configurations whose participant count exceeds the deployment's supported validator set.

### Proof of Concept
Conceptually, build a serialized `ThresholdKeys<Ristretto>` containing:

```text
u32 C::ID.len() || C::ID ||
u16 t = 65,535 ||
u16 n = 65,535 ||
u16 i = 1 ||
u8 interpolation = 1 ||       // Lagrange
32-byte canonical secret_share ||
65,535 canonical 32-byte G encodings
```

Then call:

```rust
ThresholdKeys::<Ristretto>::read(&mut serialized.as_slice())
```

The byte stream reaches `ThresholdKeys::new`, which calls `interpolation_factor` for all 65,535 participants; every Lagrange factor itself iterates over all 65,535 included participants. [3](#0-2) [5](#0-4)

### Citations

**File:** crypto/dkg/src/lib.rs (L164-178)
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
```

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

**File:** crypto/dkg/src/lib.rs (L376-379)
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
