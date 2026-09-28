### Title
Attacker-controlled `n` in `ThresholdKeys::read` causes amplified memory allocation (denial of service) - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` trusts a u16 `n` field from untrusted input and immediately executes `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` variant before verifying that the claimed elements actually exist in the stream. A 2-byte field claiming `n = 65535` forces an allocation of 65535 `C::F` slots (~2 MiB for secp256k1/ed25519 scalars) from an input of only a handful of bytes — an amplification factor near 1,000,000× per call.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` parses `t`, `n`, `i` (lines 591–602), then on interpolation tag `0` does:

```rust
0 => Interpolation::Constant({
  let mut res = Vec::with_capacity(usize::from(n));
  for _ in 0 .. n {
    res.push(C::read_F(reader)?);
  }
  res
}),
```

The `with_capacity` happens before the loop, so a short/truncated buffer still triggers the full `n`-sized allocation — the subsequent `read_F` calls fail at EOF *after* the memory is committed. The same `n` also drives a `verification_shares` loop of `n` `read_G` calls (lines 620–623), but that loop is bounded by actual input bytes; the `with_capacity` is the allocation amplifier.

This is the same bug class as the reference advisory (unbounded resource consumption driven by attacker-supplied quantities): the count of internal objects/allocations is derived entirely from bytes an unprivileged party supplies to `ThresholdKeys::read`, which is an exposed deserialization API for untrusted input.

### Impact Explanation
Any code path that feeds peer/validator-supplied bytes into `ThresholdKeys::read` (key material gossiped during DKG completion, recovery, or resharing) lets a remote unauthenticated party force ~2 MiB allocations per ~10-byte message. Repeating this trivially exhausts process memory (OOM kill of the signer/processor), i.e., a practical remote DoS of the threshold-signing node. Medium severity: each allocation is capped at u16::MAX × 32 B, so it requires repeated requests rather than a single packet, but the amplification ratio makes bandwidth cost to the attacker negligible.

### Likelihood Explanation
Reachable by any party able to submit serialized `ThresholdKeys` bytes to a deserialization endpoint — no key share, validator status, or valid signature required. Exploitation is deterministic (the capacity allocation happens unconditionally on tag `0`), requires no race or timing, and the input is only ~11 bytes of header plus the tag byte.

### Recommendation
Do not pre-allocate from untrusted counts. Replace `Vec::with_capacity(usize::from(n))` with `Vec::new()` (let it grow only as elements are actually read), and/or impose a protocol-level bound on `n` (e.g., `MAX_PARTICIPANTS`) and reject inputs where `n` exceeds the bytes remaining / a fixed cap before allocating. The same pattern should be audited in sibling readers (e.g., the `verification_shares` `HashMap` population).

### Proof of Concept

```rust
// crypto/dkg/src/lib.rs — ThresholdKeys::<C>::read trusts `n` for Vec::with_capacity
use frost::curve::Secp256k1;
use frost::ThresholdKeys;

// Build a minimal malicious header:
// [id_len=32LE][C::ID 32 bytes][t u16][n u16=0xFFFF][i u16][interpolation=0]
let mut buf = Vec::new();
buf.extend(32u32.to_le_bytes());
buf.extend(<Secp256k1 as frost::curve::Ciphersuite>::ID.as_bytes()); // 32-byte curve ID
buf.extend(1u16.to_le_bytes());            // t = 1
buf.extend(u16::MAX.to_le_bytes());        // n = 65535 -> Vec::with_capacity(65535)
buf.extend(1u16.to_le_bytes());            // i = 1
buf.push(0u8);                             // Interpolation::Constant
// Provide NO further bytes. read_F will fail at EOF, but the ~2 MiB
// allocation for 65535 C::F elements has already been performed.
let _ = ThresholdKeys::<Secp256k1>::read(&mut buf.as_slice()); // Err, after OOM-scale alloc
```

Looping this call from an input stream under attacker control allocates ~2 MiB per ~45 bytes sent until memory is exhausted.