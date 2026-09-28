### Title
Attacker-controlled participant count causes oversized allocation during `ThresholdKeys` deserialization - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` trusts the serialized `n` field and preallocates a vector containing `n` scalars before validating `ThresholdParams` or checking that the input actually contains the corresponding scalar encodings. A short malformed message can therefore trigger a multi-megabyte allocation and then fail, enabling repeated or concurrent requests to exhaust memory. [1](#0-0) 

### Finding Description
After reading the curve identifier, `ThresholdKeys::read` accepts attacker-controlled `t`, `n`, and `i` fields. For interpolation tag `0`, it immediately executes `Vec::with_capacity(usize::from(n))`, where `n` can be `u16::MAX`. Only afterward does it attempt to read `n` serialized scalars; `ThresholdParams::new` is not called until after the verification shares are parsed. [2](#0-1) 

This creates a serialized-input-to-memory amplification primitive. With a 32-byte scalar representation, `n = 65535` causes an approximately 2 MiB allocation from a prefix of only the curve identifier, parameters, and interpolation tag. The input can then end abruptly, causing an I/O error while the oversized allocation has already been performed.

### Impact Explanation
An unprivileged party able to submit serialized `ThresholdKeys` bytes can repeatedly trigger large transient allocations with tiny malformed inputs. Concurrent requests can multiply this allocation pressure, potentially causing allocator exhaustion or process termination. Because validation is delayed, even values that would later be rejected still trigger the allocation.

### Likelihood Explanation
Exploitation requires only a reachable `ThresholdKeys::read` call on untrusted bytes, which is an intended deserialization path. The payload is deterministic and does not require valid scalars, signatures, proof data, timing, or protocol privileges. Reliability depends on process memory limits and whether multiple malicious inputs can be processed concurrently.

### Recommendation
Validate `t`, `n`, and `i` with `ThresholdParams::new` immediately after reading them. Enforce an explicit maximum supported participant count before allocating, and remove `Vec::with_capacity(n)` or cap it independently of attacker input. Prefer incrementally pushing successfully decoded scalars so memory consumption remains proportional to bytes actually present, and reject oversized encodings before constructing the `HashMap` of verification shares.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs
use std::io;
use dkg::ThresholdKeys;
use dalek_ff_group::Ed25519;

fn trigger_allocation() -> io::Result<()> {
    let mut input = Vec::new();

    // Curve identifier required by ThresholdKeys::read.
    input.extend(u32::try_from(<Ed25519 as ciphersuite::Ciphersuite>::ID.len())?
        .to_le_bytes());
    input.extend(<Ed25519 as ciphersuite::Ciphersuite>::ID);

    input.extend(1u16.to_le_bytes());        // t
    input.extend(u16::MAX.to_le_bytes());    // n = 65_535
    input.extend(1u16.to_le_bytes());        // i
    input.push(0);                           // Interpolation::Constant
    // Intentionally omit all n scalar encodings.

    let _ = ThresholdKeys::<Ed25519>::read(&mut input.as_slice());
    Ok(())
}
```

This reaches `Vec::with_capacity(65535)` before the first `read_F` fails on EOF. [3](#0-2)

### Citations

**File:** crypto/dkg/src/lib.rs (L591-631)
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

    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```
