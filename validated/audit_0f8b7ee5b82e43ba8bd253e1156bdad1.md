### Title
Attacker-controlled participant count in `ThresholdKeys::read` causes amplified memory allocation DoS - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes a `u16` value `n` directly from untrusted input and immediately calls `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` coefficient vector — before any of the `n` scalars are actually read or validated. A ~15-byte input therefore forces an allocation of up to 65,535 field elements (~2 MiB for a 32-byte scalar type), a memory-amplification loop driven entirely by attacker-supplied bytes, analogous to CVE-2021-39923's attacker-triggered large allocation/loop in the PNRP dissector.

### Finding Description
The deserialization flow: [1](#0-0) 

After the curve-ID check (a handful of bytes), `t`, `n`, and `i` are read as `u16`s. `n` is fully attacker-controlled up to `u16::MAX`. If the interpolation tag byte is `0` (Constant), `Vec::with_capacity(usize::from(n))` reserves space for `n` scalars. Only afterward does the `for _ in 0 .. n { C::read_F(reader)? }` loop run — and while that loop is bounded by bytes actually present, the reservation happens unconditionally up front. Note the subsequent `verification_shares` loop (lines 620–623) reads `n` points, which *is* input-bound and not the issue; the vulnerability is specifically the eager `with_capacity` on line 608.

`ThresholdParams::new(t, n, i)` is only invoked at line 626, *after* the oversized allocation, so no parameter validation guards it.

The same pattern repeats in the permitted sink `EncryptedMessage::read` → `E::read(reader, params)` path only via `params.t()` (caller-bounded), and `SchnorrAggregate::read` (crypto/schnorr/src/aggregate.rs:77-88) uses a bare `vec![]` push loop, so it has no amplification — confirming `ThresholdKeys::read` is the anomalous site.

### Impact Explanation
`ThresholdKeys::read` is an explicitly in-scope untrusted-bytes sink. An unprivileged party that can cause a node/integrator to deserialize a `ThresholdKeys` blob (key exchange, backup import, recover/resync flows) can send ~15 bytes declaring `n = 0xFFFF` and interpolation tag `0`, forcing each call to reserve `65535 * size_of::<C::F>()` bytes. The allocation is transient (dropped when `read_F` hits EOF), so this is a CPU/allocator-pressure and transient-memory amplification primitive rather than a persistent leak: an attacker issuing these reads concurrently can drive allocator churn and transient footprint far beyond their bandwidth cost (~65,000× byte amplification per call). Denial of service via resource exhaustion — matching the CVE's availability-only impact class.

### Likelihood Explanation
Reachable wherever serialized `ThresholdKeys` cross a trust boundary (the threat model explicitly lists `ThresholdKeys::read` as an untrusted-bytes entry point). Exploitation requires only setting the `n` field; no valid scalars, proofs, or signatures are needed since the allocation precedes all validation. Severity is bounded by `u16` (max ~2 MiB per call for 32-byte fields, more for larger `F`), keeping this Medium rather than High.

### Recommendation
- Reorder `ThresholdKeys::read` to construct/validate `ThresholdParams::new(t, n, i)` *before* allocating, and reject `n` above the deployment's real maximum.
- Replace `Vec::with_capacity(n)` with incremental `push` (or `with_capacity(n.min(some_small_bound))`) so allocation never exceeds bytes actually consumed — the same chunked-read discipline already used in `networks/ethereum/src/machine.rs` `Call::read` (lines 51–60), which explicitly documents this exact DoS class.
- Apply the same check to the `verification_shares` loop for consistency.

### Proof of Concept
```rust
// crypto/dkg -- conceptual PoC for C = Ed25519 (32-byte F)
let mut buf = vec![];
buf.extend(u32::try_from(Ed25519::ID.len()).unwrap().to_le_bytes());
buf.extend(Ed25519::ID);
buf.extend(1u16.to_le_bytes());      // t
buf.extend(u16::MAX.to_le_bytes());  // n = 65535 -> with_capacity(65535 * 32B) ~= 2 MiB
buf.extend(1u16.to_le_bytes());      // i
buf.push(0);                         // Interpolation::Constant -> triggers with_capacity
// No further bytes needed; the ~2 MiB reservation occurs before read_F fails on EOF
let _ = ThresholdKeys::<Ed25519>::read(&mut buf.as_slice());
// Repeat in parallel; each ~15-byte message forces a ~2 MiB transient allocation.
```

Uncertain aspects: whether production call sites cap the input length or concurrency before invoking `ThresholdKeys::read` could not be fully verified within the search scope; the amplification factor is exact, but realized impact depends on the caller's exposure of this reader to unauthenticated input.

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
