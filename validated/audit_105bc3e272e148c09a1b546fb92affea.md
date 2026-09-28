### Title
Unbounded deserialization work in `ThresholdKeys::read`: attacker-controlled `n` drives allocation and expensive point decoding before parameter validation - (File: crypto/dkg/src/lib.rs)

### Summary
The external report describes a code path that silently skips a length constraint enforced elsewhere (`maxNumberLength` in the async parser), enabling memory/CPU exhaustion from attacker-controlled input (CWE-770). The analog in Serai's in-scope code is `ThresholdKeys::read` in `crypto/dkg/src/lib.rs`: all length parameters (`t`, `n`, `i`) are attacker-controlled bytes, yet they are trusted to size allocations and loop bounds *before* `ThresholdParams::new` / `ThresholdKeys::new` ever validate them. Validation only happens at the very end of the function, after all resource consumption has already occurred.

### Finding Description
`ThresholdKeys::read` at `crypto/dkg/src/lib.rs:574` reads `t`, `n`, and `i` directly from the input stream:

```rust
// crypto/dkg/src/lib.rs:591-616
let (t, n, i) = { /* three attacker-controlled u16s */ };

let mut interpolation = [0];
reader.read_exact(&mut interpolation)?;
let interpolation = match interpolation[0] {
  0 => Interpolation::Constant({
    let mut res = Vec::with_capacity(usize::from(n));      // alloc n * sizeof(F) up-front
    for _ in 0 .. n {
      res.push(C::read_F(reader)?);                        // n scalar reads
    }
    res
  }),
  ...
};

let secret_share = Zeroizing::new(C::read_F(reader)?);

let mut verification_shares = HashMap::new();
for l in (1 ..= n).map(Participant) {
  verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);  // n point decompressions
}

ThresholdKeys::new(
  ThresholdParams::new(t, n, i).map_err(io::Error::other)?,   // validation ONLY here, at the end
  interpolation, secret_share, verification_shares,
)
```

Mirroring the jackson-core bug structure:

1. **Up-front allocation from untrusted length**: `Vec::with_capacity(usize::from(n))` (`crypto/dkg/src/lib.rs:608`) allocates `n * size_of::<F>()` bytes purely on the attacker's say-so. With `n = u16::MAX` (65535), a roughly 2 MB allocation is performed even if the stream contains only ~10 bytes — the first `C::read_F` then fails on EOF and the allocation is dropped. This is allocation sized by unvalidated input, repeated arbitrarily per call.

2. **Expensive work before consistency validation**: the `verification_shares` loop (`crypto/dkg/src/lib.rs:621-623`) performs up to 65,535 full group-element decompressions (`read_G` — point decompression and validity checks), each costing tens of microseconds of CPU. A ~2 MB message claiming `n = 65535` therefore causes several seconds of CPU work even when the header is trivially invalid (e.g., `t = 0`, `i = 0`, `i > n`, `t > n`), because `ThresholdParams::new` is only invoked on line 626, after all parsing work has completed. The "constraint check" (`ThresholdParams::new` rejecting `t > n`, `i > n`, `i == 0`, etc.) exists — it is just invoked after the work it should have prevented, exactly like the async parser calling `_valueComplete()` without ever reaching `resetInt()`/`validateIntegerLength()`.

### Impact Explanation
An unprivileged party supplying bytes to `ThresholdKeys::read` (one of the reachable untrusted-bytes APIs) can cause repeated multi-megabyte transient allocations and multi-second CPU bursts (65k point decompressions) per ~2 MB of input, with no cost proportional to the input's validity. Repeated submissions constitute a memory/CPU exhaustion DoS against the process parsing key material — CWE-770, the same bug class as the advisory. Severity: Medium.

### Likelihood Explanation
`ThresholdKeys::read` is a public deserialization entry point explicitly in scope for untrusted input. The attacker controls `n` (2 bytes, up to 65535) and the interpolation selector (1 byte); no authentication or pre-check bounds the declared size before it drives allocation and decompression loops. The only mitigating factor is that group-element reads require the corresponding bytes to actually be present, so peak CPU cost scales with input size — but the `Vec::with_capacity` allocation and the full `verification_shares` decompression loop both precede any semantic validation of `t`/`n`/`i`, so even self-evidently invalid parameter triples incur the full parsing cost.

### Recommendation
Validate `ThresholdParams::new(t, n, i)` immediately after reading the header fields, before allocating the `Constant` interpolation vector or reading any scalars/points. Additionally, impose a documented maximum `n` before `Vec::with_capacity`/`HashMap` growth, or read scalars/points incrementally into `Vec::new()`/`HashMap::new()` so memory usage is bounded by bytes actually present rather than the declared count.

### Proof of Concept
```rust
// Conceptual PoC against ThresholdKeys::read
// (e.g., C = frost::curve::Secp256k1 or Ristretto)

fn dos_threshold_keys() {
    // Header: curve ID (correct), then t=0, n=65535, i=0 (invalid params,
    // but only checked AFTER the reads/allocations below)
    let mut buf = vec![];
    buf.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
    buf.extend(C::ID);
    buf.extend(0u16.to_le_bytes());      // t = 0 (invalid)
    buf.extend(u16::MAX.to_le_bytes());  // n = 65535
    buf.extend(0u16.to_le_bytes());      // i = 0 (invalid Participant)
    buf.push(0);                         // Interpolation::Constant -> Vec::with_capacity(65535)

    // Case A (memory): stop here. Vec::with_capacity(65535 * size_of::<F>())
    // (~2 MB) is allocated before the first read_F fails on EOF.
    let _ = ThresholdKeys::<C>::read(&mut buf.as_slice());

    // Case B (CPU): append 65535 valid point encodings (~2 MB total).
    // 65k decompressions run to completion, THEN ThresholdParams::new(t=0, n, i=0)
    // rejects — all work wasted on a header-invalid input.
}
```

Citations: [1](#0-0)

### Citations

**File:** crypto/dkg/src/lib.rs (L591-632)
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
  }
```
