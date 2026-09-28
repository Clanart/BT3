### Title
Unauthenticated `ThresholdKeys` deserialization enables memory amplification - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` trusts the serialized `n` participant count before validating the threshold parameters or confirming that the remaining serialized payload exists. For the `Interpolation::Constant` variant, an attacker can provide a short prefix containing `n = 65535` and cause the deserializer to allocate a vector for 65,535 field elements immediately. Repeating the operation can exhaust available memory and deny service to the process.

### Finding Description
`ThresholdKeys::read` parses `t`, `n`, and `i` directly from the input stream. When the next byte selects `Interpolation::Constant`, it calls `Vec::with_capacity(usize::from(n))` before invoking `ThresholdParams::new` or requiring that any coefficient bytes are present.

The vulnerable sequence is: [1](#0-0) 

Only after reading all `n` coefficients, the secret share, and `n` verification shares does the function call `ThresholdParams::new(t, n, i)`: [2](#0-1) 

Therefore, a payload consisting only of the curve ID, `t`, `n`, `i`, and interpolation tag can trigger a large allocation. For typical 32-byte field elements, setting `n` to `u16::MAX` allocates approximately 2 MiB of capacity per deserialization attempt. `read_F` subsequently fails on EOF, but the allocation has already occurred and may persist transiently until the error path drops the vector.

### Impact Explanation
An unprivileged caller that can submit untrusted bytes to `ThresholdKeys::read` can amplify a small request into repeated multi-megabyte allocations. Calling the API concurrently or repeatedly can exhaust process memory and produce a denial of service. The issue is reachable through the explicitly exposed `ThresholdKeys::read` parser; it does not require a validator key, leaked secret, malformed curve implementation, or malicious peer assumptions.

### Likelihood Explanation
The payload is trivial to construct. For `Interpolation::Constant`, only enough input is needed to pass the curve-ID check and supply `t`, `n`, `i`, and byte `0`; `Vec::with_capacity(n)` executes before any coefficient `read_exact` can fail. There is no transaction-size limit, signature requirement, or prior parameter validation before the allocation.

### Recommendation
Validate `t`, `n`, and `i` with `ThresholdParams::new` before allocating or parsing any variable-length structures. Additionally, avoid preallocating from the serialized `n` unless it is first compared to a protocol-defined maximum. A safer pattern is to construct `ThresholdParams` immediately after reading `t`, `n`, and `i`, reject excessive values, then incrementally populate the coefficient vector without trusting `n` as an allocation size.

### Proof of Concept
```rust
use std::io;
use dkg::{Interpolation, Participant, ThresholdKeys};
use ciphersuite::Ciphersuite;

fn malicious_payload<C: Ciphersuite>() -> Vec<u8> {
  let mut payload = vec![];

  // Valid curve-ID framing.
  payload.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
  payload.extend(C::ID);

  // t = 1, n = u16::MAX, i = 1.
  payload.extend(1u16.to_le_bytes());
  payload.extend(u16::MAX.to_le_bytes());
  payload.extend(1u16.to_le_bytes());

  // Interpolation::Constant.
  payload.push(0);

  // No coefficient bytes follow. Vec::with_capacity(65535) has
  // nevertheless already been requested before read_F hits EOF.
  payload
}

fn exercise<C: Ciphersuite>() -> io::Result<ThresholdKeys<C>> {
  let bytes = malicious_payload::<C>();
  ThresholdKeys::<C>::read(&mut bytes.as_slice())
}
```

### Citations

**File:** crypto/dkg/src/lib.rs (L591-613)
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
```

**File:** crypto/dkg/src/lib.rs (L620-631)
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
```
