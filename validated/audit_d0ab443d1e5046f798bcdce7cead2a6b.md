### Title
Attacker-controlled `n` in serialized `ThresholdKeys` drives unvalidated, pre-failure heap allocation - ([File: crypto/dkg/src/lib.rs](Kirstentat/serai--012))

### Summary
The external report is a memory-exhaustion/DoS class: attacker-supplied input triggers allocation far larger than the input itself. The in-scope analog is `ThresholdKeys::read` in `crypto/dkg/src/lib.rs`, which reads the participant count `n` directly from untrusted bytes and uses it to pre-allocate a vector (`Vec::with_capacity(usize::from(n))`) and to bound a `HashMap` insertion loop — all *before* `ThresholdParams::new(t, n, i)` validates `n`. A ~10-byte crafted blob therefore forces allocation proportional to a 16-bit attacker-chosen value, and forces the reader to attempt `2n` deserializations, giving an unprivileged party an allocation-amplification DoS against anything calling `ThresholdKeys::read` on received bytes.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` as raw little-endian `u16`s straight from the reader [1](#0-0) . It then branches on an interpolation tag; the `Constant` variant immediately executes `Vec::with_capacity(usize::from(n))` — allocating `n * size_of::<C::F>()` (~32 B per element, i.e. ~2 MiB for `n = 0xFFFF`) — before a single element or any parameter validity is checked [2](#0-1) . Independently of the interpolation variant, it then loops `for l in (1 ..= n)` inserting into `verification_shares`, attempting `n` group-element reads [3](#0-2) . Only after this work does it call `ThresholdParams::new(t, n, i)`, the first place `n` is bounds-checked against the protocol's real participant count [4](#0-3) . The byte count consumed before validation can be as small as the fixed header (~10 bytes), while the allocation is attacker-scaled.

### Impact Explanation
`ThresholdKeys::read` is one of the enumerated untrusted-byte entry points. An unprivileged party supplying crafted bytes causes each deserialization to allocate ~2 MiB and attempt up to ~131k scalar/point reads before failing. Issued in a loop or concurrently (e.g., by spamming malformed key blobs at any component that re-reads serialized `ThresholdKeys`, including restart/recovery paths where these blobs are re-ingested), this produces sustained heap churn and CPU burn — a denial of service of the same class as the tiffcrop leak: small crafted input → disproportionate resource consumption → crash/availability loss. Impact is capped at Medium because `n` is a `u16`, so a single call's damage is bounded (~4 MiB total across both structures), and the memory is freed on the error path rather than permanently leaked.

### Likelihood Explanation
Likelihood is moderate: any code path that deserializes attacker-influenced `ThresholdKeys` bytes is reachable with public inputs, and the trigger requires no protocol position, stake, or valid signature — just bytes shaped as `id_len || id || t || n=0xFFFF || i || 0x00`. Mitigating factors: the `u16` bound caps per-call cost, `Vec::with_capacity` failure aborts rather than hangs, and the wasted memory is reclaimed when the `Err` propagates, so exploitation requires repeated submissions rather than a single shot.

### Recommendation
Validate `n` before trusting it: read `t`, `n`, `i`, construct and check `ThresholdParams::new(t, n, i)` first, reject `n == 0` and any `n` above the protocol's supported maximum, and only then size the interpolation vector and the `verification_shares` loop from the validated value. For `Interpolation::Constant`, also confirm at least `n` elements remain in the stream before `with_capacity`, or drop the pre-allocation and `push` into an empty `Vec`.

### Proof of Concept
Feed `ThresholdKeys::<C>::read` a minimal buffer: `C::ID` length-prefixed, then `t = 0x0001`, `n = 0xFFFF`, `i = 0x0001`, interpolation tag `0x00` (Constant), followed by EOF. Execution reaches `Vec::with_capacity(65535)` at `crypto/dkg/src/lib.rs:608` — an ~2 MiB allocation — and the subsequent `read_F` loop iterates until `read_exact` errors; with a non-empty tail it would proceed to the `(1 ..= 65535)` `verification_shares` loop at lines 620–623. The `ThresholdParams::new` validity check at lines 625–626 is never reached on the error path, confirming the allocation precedes all bounds enforcement.

### Citations

**File:** crypto/dkg/src/lib.rs (L591-602)
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
```

**File:** crypto/dkg/src/lib.rs (L604-616)
```rust
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

**File:** crypto/dkg/src/lib.rs (L620-623)
```rust
    let mut verification_shares = HashMap::new();
    for l in (1 ..= n).map(Participant) {
      verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
    }
```

**File:** crypto/dkg/src/lib.rs (L625-631)
```rust
    ThresholdKeys::new(
      ThresholdParams::new(t, n, i).map_err(io::Error::other)?,
      interpolation,
      secret_share,
      verification_shares,
    )
    .map_err(io::Error::other)
```
