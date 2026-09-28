### Title
Unauthenticated serialized `n` forces oversized allocation in `ThresholdKeys::read` - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` trusts the serialized participant count `n` before validating the threshold parameters or checking that the input contains the corresponding scalar data. When the interpolation discriminator selects `Interpolation::Constant`, it immediately allocates a vector with capacity for up to 65,535 field elements based solely on two attacker-controlled bytes. [1](#0-0) 

### Finding Description
The parser reads `t`, `n`, and `i` directly from the input. [2](#0-1)  If the next byte is `0`, the code executes `Vec::with_capacity(usize::from(n))` before attempting to deserialize even one coefficient. [3](#0-2)  The serialized values are not passed through `ThresholdParams::new` until after the interpolation vector, secret share, and all `n` verification shares have been read. [4](#0-3)  Consequently, a very short input can force allocation for the maximum `u16` participant count and then fail on the first missing field element.

### Impact Explanation
An unprivileged party that can submit untrusted bytes to `ThresholdKeys::read` can amplify a small input into repeated multi-megabyte allocations, depending on the selected ciphersuite’s scalar size. Repeated requests can exhaust memory or degrade the process handling DKG/FROST state deserialization. This maps to the reported excessive-allocation class because allocation size is derived from an unauthenticated length-like field rather than the amount of data actually supplied.

### Likelihood Explanation
The trigger requires only a valid curve ID followed by `t`, `n`, `i`, and a `Constant` interpolation discriminator; no valid scalar, group element, proof, signature, key share, or complete serialization is required. The affected reader is a public deserialization API, and the malicious prefix can be generated without cryptographic knowledge or privileged access. [5](#0-4) 

### Recommendation
Do not allocate capacity from the serialized `n`. Initialize an empty `Vec`, parse the coefficients incrementally, and reject `n` values above the protocol’s configured participant limit before reading variable-sized data. Where the reader supports it, also verify that the declared structure length is consistent with the available input before allocating or iterating.

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use dkg::ThresholdKeys;

fn trigger<C: Ciphersuite>() {
  let mut input = Vec::new();

  // Curve ID header expected by ThresholdKeys::read.
  input.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
  input.extend(C::ID);

  // t = 1, n = u16::MAX, i = 1.
  input.extend(1u16.to_le_bytes());
  input.extend(u16::MAX.to_le_bytes());
  input.extend(1u16.to_le_bytes());

  // Interpolation::Constant. This immediately reserves capacity for
  // 65,535 field elements before the first scalar is read.
  input.push(0);

  // No scalar or verification-share bytes are supplied.
  let result = ThresholdKeys::<C>::read(&mut input.as_slice());
  assert!(result.is_err());
}
```

The allocation occurs at `Vec::with_capacity(usize::from(n))`, before `C::read_F(reader)` observes the truncated input. [3](#0-2)

### Citations

**File:** crypto/dkg/src/lib.rs (L573-613)
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
```

**File:** crypto/dkg/src/lib.rs (L618-631)
```rust
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
