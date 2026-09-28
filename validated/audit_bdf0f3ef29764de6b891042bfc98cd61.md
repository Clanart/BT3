### Title
Attacker-controlled participant count forces multi-megabyte allocation during `ThresholdKeys` deserialization - ([File: crypto/dkg/src/lib.rs])

### Summary

`ThresholdKeys::read` trusts the serialized `n` value before validating the threshold parameters and immediately uses it as the capacity for the constant-interpolation vector. [1](#0-0) [2](#0-1) 

### Finding Description

The deserializer reads `t`, `n`, and participant index `i` as three `u16` values, so a malformed key blob can specify `n = 65,535`. [1](#0-0) 

If the following interpolation byte selects `Interpolation::Constant`, the code calls `Vec::with_capacity(usize::from(n))` before attempting to read the first scalar and before `ThresholdParams::new` validates the supplied values. [2](#0-1) [3](#0-2) 

Because the allocation happens before any scalar data is required, a short malformed buffer ending immediately after the interpolation marker still reserves storage for 65,535 field elements. [4](#0-3) 

### Impact Explanation

Each malformed `ThresholdKeys` input can force a multi-megabyte allocation from a prefix whose size is independent of `n`. [2](#0-1) 

Repeated concurrent submissions can therefore amplify small public inputs into substantial allocator pressure and deny service even though parsing subsequently fails while reading the missing coefficients. [5](#0-4) 

### Likelihood Explanation

The vulnerable path is directly reachable whenever untrusted bytes are passed to `ThresholdKeys::read`, and the attacker controls all bytes needed to set `n = 65,535` and select constant interpolation. [6](#0-5) [7](#0-6) 

The attacker does not need valid keys, threshold parameters, scalar encodings, or enough bytes to satisfy the declared count because the oversized allocation precedes validation and coefficient parsing. [2](#0-1) [3](#0-2) 

### Recommendation

Construct and validate `ThresholdParams` before allocating `Vec::with_capacity(n)`, and enforce a protocol-appropriate maximum participant count before deserializing the interpolation coefficients. [3](#0-2) 

Alternatively, grow the vector incrementally while reading coefficients so allocated memory remains proportional to bytes actually supplied by the peer. [5](#0-4) 

### Proof of Concept

The following malformed input supplies a valid ciphersuite ID, invalid threshold parameters, `n = u16::MAX`, and constant interpolation, then ends before any coefficient is present. [8](#0-7) [7](#0-6) 

```rust
use dkg::ThresholdKeys;
use ciphersuite::Ciphersuite;

fn allocation_bomb<C: Ciphersuite>() {
    let mut bytes = Vec::new();

    // Valid ciphersuite framing.
    bytes.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
    bytes.extend(C::ID);

    bytes.extend(0u16.to_le_bytes());              // t, invalid but not yet checked
    bytes.extend(u16::MAX.to_le_bytes());          // n = 65,535
    bytes.extend(1u16.to_le_bytes());              // i = 1
    bytes.push(0);                                 // Interpolation::Constant

    // Vec::with_capacity(65_535) runs before read_F fails at EOF.
    assert!(ThresholdKeys::<C>::read(&mut bytes.as_slice()).is_err());
}
```

### Citations

**File:** crypto/dkg/src/lib.rs (L573-575)
```rust
  /// Read keys from a type satisfying `std::io::Read`.
  pub fn read<R: io::Read>(reader: &mut R) -> io::Result<ThresholdKeys<C>> {
    {
```

**File:** crypto/dkg/src/lib.rs (L578-586)
```rust
      let mut id_len = [0; 4];
      reader.read_exact(&mut id_len)?;
      if u32::try_from(C::ID.len()).unwrap().to_le_bytes() != id_len {
        Err(different())?;
      }

      let mut id = vec![0; C::ID.len()];
      reader.read_exact(&mut id)?;
      if id != C::ID {
```

**File:** crypto/dkg/src/lib.rs (L591-611)
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
```

**File:** crypto/dkg/src/lib.rs (L625-630)
```rust
    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
```
