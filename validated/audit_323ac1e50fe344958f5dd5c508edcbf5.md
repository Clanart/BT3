### Title
Attacker-controlled participant count in `ThresholdKeys::read` enables memory-allocation amplification DoS - (File: crypto/dkg/src/lib.rs)

### Summary

`ThresholdKeys::read` deserializes a key-share blob by trusting an attacker-controlled `n` field (a `u16` read from the stream) and immediately uses it to size allocations and a read loop *before* validating it against any legitimate threshold parameters. A very short input can therefore trigger a disproportionately large allocation — the same bug class as CVE-2021-28652 (incorrect parser validation letting a small query string cause memory exhaustion DoS). [1](#0-0) 

### Finding Description

In `ThresholdKeys::read`, `t`, `n`, and `i` are read as raw `u16`s from the untrusted reader, and `n` is used directly:

```rust
0 => Interpolation::Constant({
  let mut res = Vec::with_capacity(usize::from(n));
  for _ in 0 .. n {
    res.push(C::read_F(reader)?);
  }
  res
}),
``` [2](#0-1) 

A 2-byte `n` of `0xFFFF` causes `Vec::with_capacity(65535)` — ~2 MiB for a 32-byte scalar type, more for larger fields — before a single byte of payload is validated. Immediately afterwards, a `HashMap` is filled with `n` verification-share point reads:

```rust
let mut verification_shares = HashMap::new();
for l in (1 ..= n).map(Participant) {
  verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
}
``` [3](#0-2) 

Only *after* these allocations and reads does `ThresholdParams::new(t, n, i)` validate the parameters (`ThresholdKeys::new` is called at the end). The parser thus performs length-driven work on unvalidated length data — the identical root cause to the Squid Cache Manager bug.

Reachability: `ThresholdKeys::read` is an explicitly listed untrusted-bytes entry point, so an unprivileged party that can cause a node to deserialize supplied key material reaches this path. The relevant upstream reader `read_F` / `read_G` itself errors on truncation, but the speculative `with_capacity` allocation occurs before the first `read_F` call fails. [4](#0-3) 

### Impact Explanation

An attacker can force allocation of roughly `n × sizeof(F)` bytes (~2 MiB per call with `n = 65535` for a 32-byte field) plus `HashMap`/`Vec` overhead for up to 65535 entries, using an input of only a handful of bytes — an amplification factor exceeding 10⁵×. Repeated or concurrent deserialization requests cause memory pressure/exhaustion and process DoS of the validator/coordinator component handling the blob. The allocator charge is transient (freed when `read` errors), matching the "repeated requests → cumulative memory exhaustion" shape of the CVE rather than a permanent leak.

### Likelihood Explanation

Reachability depends on an integrator feeding attacker-controlled bytes to `ThresholdKeys::read`, which is the documented deserialization path for threshold key material received over the wire. Per-request cost is bounded (`n` is `u16`-limited, ~4–8 MiB worst case including points), so meaningful impact requires repeated requests — consistent with a Medium-severity availability analog rather than a single-shot kill. No secret leakage or incorrect cryptography results; the failure is purely the missing validation of `n` before allocation.

### Recommendation

Validate `n` against `ThresholdParams` limits (and against the actual declared `t`) *before* using it in `with_capacity` or loop bounds — i.e., perform `ThresholdParams::new` checks (or a documented `MAX_N` sanity bound) immediately after reading `t`, `n`, `i`, and only then allocate. Alternatively, use `Vec::new()` and grow incrementally so the allocation is proportional to bytes actually present in the stream, as done for chunked reads elsewhere in the codebase (e.g., `networks/ethereum/src/machine.rs` reads `data_len` in 1 KB chunks specifically to defeat this DoS pattern). [5](#0-4) 

### Proof of Concept

```rust
// Craft a ThresholdKeys blob for any Ciphersuite C:
let mut blob = vec![];
blob.extend((C::ID.len() as u32).to_le_bytes()); // correct ID length
blob.extend(C::ID);                             // correct ID
blob.extend(1u16.to_le_bytes());                // t = 1
blob.extend(65535u16.to_le_bytes());            // n = 65535 (attacker-chosen)
blob.extend(1u16.to_le_bytes());                // i = 1
blob.push(0);                                   // Interpolation::Constant
// No further bytes needed.
// Calling ThresholdKeys::<C>::read(&mut blob.as_slice()) performs
// Vec::with_capacity(65535) (~2 MiB+ for a 32-byte field) before read_F fails
// on the exhausted reader. Repeating this call concurrently amplifies memory
// usage proportionally to request count, not input size.
```

Note: I verified the allocation-before-validation ordering directly; the total bounded cost (~8 MiB/call including the `verification_shares` loop when data is present) caps per-request impact, so this is strongest as an amplification DoS — Medium severity, mirroring the CVE's own Medium rating.

### Citations

**File:** crypto/dkg/src/lib.rs (L591-623)
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
```

**File:** crypto/ciphersuite/src/lib.rs (L74-83)
```rust
  fn read_F<R: Read>(reader: &mut R) -> io::Result<Self::F> {
    let mut encoding = <Self::F as PrimeField>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    // ff mandates this is canonical
    let res = Option::<Self::F>::from(Self::F::from_repr(encoding))
      .ok_or_else(|| io::Error::other("non-canonical scalar"));
    encoding.as_mut().zeroize();
    res
  }
```

**File:** networks/ethereum/src/machine.rs (L51-60)
```rust
    // A valid DoS would be to claim a 4 GB data is present for only 4 bytes
    // We read this in 1 KB chunks to only read data actually present (with a max DoS of 1 KB)
    let mut data = vec![];
    while data_len > 0 {
      let chunk_len = data_len.min(1024);
      let mut chunk = vec![0; chunk_len];
      reader.read_exact(&mut chunk)?;
      data.extend(&chunk);
      data_len -= chunk_len;
    }
```
