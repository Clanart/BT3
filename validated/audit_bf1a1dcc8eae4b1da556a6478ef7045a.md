### Title
Attacker-controlled participant count causes disproportionate allocation in `ThresholdKeys::read` - (`crypto/dkg/src/lib.rs`)

### Summary
`ThresholdKeys::read` trusts the serialized `n` value and eagerly allocates space for `n` field elements when the interpolation variant is `Constant`, before validating the threshold parameters or confirming that the scalar data is present. A short attacker-controlled encoding can therefore force an allocation for up to 65,535 scalars and then fail at the first missing field element. Repeated or concurrent submissions can amplify a small amount of input into significant allocator pressure and memory consumption. [1](#0-0) [2](#0-1) 

### Finding Description
The deserializer reads `t`, `n`, and `i` directly from attacker-controlled bytes, with `n` represented as an unrestricted `u16`. If the next byte selects `Interpolation::Constant`, it immediately executes `Vec::with_capacity(usize::from(n))` before attempting to read any scalar. The resulting allocation is based solely on the declared count rather than the amount or structure of data actually available. [1](#0-0) 

`ThresholdParams::new(t, n, i)` is not invoked until after all interpolation coefficients, the secret share, and `n` verification shares have been read. Consequently, malformed input declaring `n = 0xffff` can trigger the full coefficient-vector allocation even when the input ends immediately after the interpolation tag. [3](#0-2) 

The affected API is `ThresholdKeys::read`, which is an explicitly reachable untrusted-byte deserialization boundary. For a typical 32-byte scalar representation, each malformed message can request roughly 2 MiB of vector capacity from only a small header; curves with larger field representations amplify this further. [4](#0-3) 

### Impact Explanation
An unprivileged party able to submit bytes to `ThresholdKeys::read` can repeatedly or concurrently create large temporary allocations using minimal input and without supplying the declared data. On memory-constrained services or under concurrent requests, this can exhaust allocator capacity, trigger aborts on allocation failure, or degrade availability for other protocol operations. [5](#0-4) 

### Likelihood Explanation
The trigger requires only a valid curve identifier followed by `n = 0xffff`, a nonzero participant index, and the `Constant` interpolation tag. No valid scalars, points, signatures, keys, or privileged protocol role are required because the allocation occurs before parameter validation and before the first scalar read fails. [6](#0-5) 

### Recommendation
Validate `ThresholdParams::new(t, n, i)` immediately after reading `t`, `n`, and `i`, and reject unreasonable protocol participant counts before allocating. Also remove the eager `Vec::with_capacity(n)` or cap it based on remaining input length and an explicit protocol maximum, so malformed messages cannot reserve memory for elements that were never supplied. [7](#0-6) 

### Proof of Concept
The following input reaches the vulnerable allocation while containing no interpolation coefficients:

```rust
use frost::curve::Ristretto;
use frost::ThresholdKeys;
use ciphersuite::Ciphersuite;

let mut bytes = Vec::new();

// Valid curve identifier.
bytes.extend(u32::try_from(Ristretto::ID.len()).unwrap().to_le_bytes());
bytes.extend(Ristretto::ID);

// t = 1, n = 65535, i = 1.
bytes.extend(1u16.to_le_bytes());
bytes.extend(u16::MAX.to_le_bytes());
bytes.extend(1u16.to_le_bytes());

// Interpolation::Constant, followed by no scalar data.
bytes.push(0);

// This allocates capacity for 65,535 scalars before returning an EOF error.
assert!(ThresholdKeys::<Ristretto>::read(&mut bytes.as_slice()).is_err());
```

An allocation-tracking global allocator or RSS measurement will show the `Vec::with_capacity(usize::from(n))` reservation before the first `C::read_F(reader)` call fails. [8](#0-7)

### Citations

**File:** crypto/dkg/src/lib.rs (L573-630)
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
```
