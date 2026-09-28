### Title
Unbounded attacker-controlled allocation in `ThresholdKeys::read` enables memory exhaustion - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::read` trusts the serialized participant count `n` before validating the threshold parameters or confirming that the claimed data is present. When the interpolation marker selects `Interpolation::Constant`, it immediately calls `Vec::with_capacity(usize::from(n))`. Because `n` is a serialized `u16`, a short input can force an allocation for up to 65,535 field elements. With a streaming reader, the allocation is retained while the peer delays or withholds the following scalar bytes, allowing concurrent malformed deserialization attempts to exhaust memory.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` directly from attacker-controlled bytes at `crypto/dkg/src/lib.rs:591-601`. It then reads the interpolation tag and, for tag `0`, allocates `Vec::with_capacity(usize::from(n))` before reading any of the promised coefficients at `crypto/dkg/src/lib.rs:604-613`. `ThresholdParams::new` is not called until after deserialization of all coefficients, the secret share, and verification shares at `crypto/dkg/src/lib.rs:625-630`.

A valid serialized prefix for a supported ciphersuite can therefore request a multi-megabyte allocation using only a few bytes:

- `C::ID.len()` and `C::ID`
- `t = 1`
- `n = 65535`
- `i = 1`
- interpolation tag `0`

For a 32-byte scalar field, the requested capacity is approximately 2 MiB. For a larger field such as Ed448, it is larger still.

### Impact Explanation
An unauthenticated caller able to submit bytes to `ThresholdKeys::read` can repeatedly cause allocations disproportionate to the bytes sent. If the underlying reader is backed by a network stream, the allocated vector remains alive while `read_exact` waits for coefficients that the attacker never sends. Concurrent stalled reads can consume substantial memory and deny service through allocator pressure or process termination.

Even with an in-memory reader, repeated calls force multi-megabyte allocations and initialization bookkeeping before immediately failing on EOF, amplifying CPU and allocator work relative to the request size.

### Likelihood Explanation
The affected field is directly serialized and attacker-controlled. No valid cryptographic material, private key, participant status, or complete payload is required to reach the allocation; only the curve ID prefix and a small parameter header are needed. Exploitability depends on the embedding application exposing deserialization of `ThresholdKeys` to untrusted input and using a reader that can block, but the vulnerable allocation occurs inside Serai’s production deserializer.

### Recommendation
Validate `t`, `n`, and `i` with `ThresholdParams::new` immediately after reading them and before interpreting the interpolation variant. Do not preallocate from serialized lengths. For `Interpolation::Constant`, either:

- read coefficients into a `Vec` without `with_capacity`, allowing growth proportional to bytes actually supplied; or
- impose a documented protocol-level maximum `n` before allocation.

The same principle should be applied to other deserializers: derive capacities only from already-validated local protocol parameters, not untrusted serialized counts.

### Proof of Concept
```rust
use std::io;
use frost::{curve::{Ciphersuite, Secp256k1}, ThresholdKeys};

fn malicious_payload() -> Vec<u8> {
    let mut bytes = Vec::new();

    // Curve identifier expected by ThresholdKeys::<Secp256k1>::read.
    bytes.extend_from_slice(
        &(u32::try_from(Secp256k1::ID.len()).unwrap()).to_le_bytes(),
    );
    bytes.extend_from_slice(Secp256k1::ID);

    // t = 1, n = 65535, i = 1.
    bytes.extend_from_slice(&1u16.to_le_bytes());
    bytes.extend_from_slice(&u16::MAX.to_le_bytes());
    bytes.extend_from_slice(&1u16.to_le_bytes());

    // Interpolation::Constant. This is sufficient to reach
    // Vec::with_capacity(65535); the omitted coefficient bytes
    // subsequently make deserialization fail or stall.
    bytes.push(0);

    bytes
}

fn main() -> io::Result<()> {
    let mut payload = malicious_payload().as_slice();
    let _ = ThresholdKeys::<Secp256k1>::read(&mut payload);
    Ok(())
}
```

The relevant allocation occurs before any coefficient or threshold-parameter validation:

```rust
0 => Interpolation::Constant({
  let mut res = Vec::with_capacity(usize::from(n));
  for _ in 0 .. n {
    res.push(C::read_F(reader)?);
  }
  res
})
```