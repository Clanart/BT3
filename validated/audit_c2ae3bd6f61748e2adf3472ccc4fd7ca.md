### Title
Attacker-controlled `n` triggers disproportionate pre-allocation in `ThresholdKeys::read` — memory exhaustion DoS - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` trusts the serialized `n` (participant count, u16) before reading the body. It immediately performs `Vec::with_capacity(n)` and builds a `HashMap` loop for `n` verification shares, allocating megabytes of memory after consuming only a handful of attacker-controlled bytes. This is the same bug class as CVE-2021-28651: a parser-level buffer-management flaw where a tiny input causes disproportionate memory consumption.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` deserializes `t`, `n`, and `i` from raw bytes. `n` is read as an unrestricted `u16` (up to 65,535) with no check that it is consistent with the actual set or the remaining input length. If the interpolation tag is `0` (Constant interpolation), the code executes `Vec::with_capacity(usize::from(n))` before attempting any further reads; it then pushes `n` `C::F` scalars and inserts `n` verification shares into a `HashMap` keyed by `Participant(1..=n)`. [1](#0-0) 

A caller feeding attacker bytes into `ThresholdKeys::read` (an explicitly permitted untrusted-input sink) therefore causes:

- An immediate `with_capacity(n)` allocation of `n * size_of::<C::F>()` bytes — up to ~2 MB for the scalar vector — before the first `read_F` is even attempted.
- A second `n`-entry `HashMap` and `n` point allocations while reading `read_G` in a loop over `(1..=n)`.

Each invocation requires only the ~40-byte header (curve ID length, curve ID, `t`, `n`, `i`, interpolation byte) to trigger the capacity reservation; the subsequent `read_exact` failures come *after* the allocation. Repeated deserialization requests (e.g., per-message or per-connection key-loading paths) let an unprivileged party amplify tens of bytes into multi-megabyte transient allocations, exhausting memory — the exact amplification pattern of the Squid URN parser leak.

### Impact Explanation
Denial of service through memory exhaustion. An attacker supplies crafted serialized `ThresholdKeys` blobs declaring `n = 0xFFFF` with Constant interpolation; each parse performs ~2–4 MB of allocation proportional to the forged count, not to the bytes sent. Sustained requests exhaust available memory and crash or stall the validator/processor process, halting signing operations.

### Likelihood Explanation
Reachable wherever `ThresholdKeys::read` is invoked on bytes influenced by an external party (coordinator-supplied or peer-supplied key material, session/state rehydration). The trigger requires no valid signature, no threshold participation, and no secret — only control over a few header bytes. The allocation happens unconditionally on the untrusted count. Whether a production caller exposes this read to unauthenticated input determines exploitability; the parsing defect itself is unconditional.

### Recommendation
- Validate `n` against `ThresholdParams` limits (e.g., `n >= t`, `n <= MAX_PARTICIPANTS`) before allocating.
- Replace `Vec::with_capacity(n)` with incremental `push`/`read_F` so allocation grows only with data actually present (the same chunked-read fix applied in `Call::read` in `networks/ethereum/src/machine.rs`, which explicitly guards this exact "claim 4 GB in 4 bytes" DoS).
- Size the `verification_shares` `HashMap` lazily rather than iterating `1..=n` from the untrusted count, or enforce that `n` matches the locally-known participant set before reading any shares.

### Proof of Concept
Construct a byte stream: `[len(C::ID) as u32 LE][C::ID][t=1 u16][n=0xFFFF u16][i=1 u16][interpolation=0x00]` followed by truncation. Calling `ThresholdKeys::<C>::read(&mut bytes)` performs `Vec::with_capacity(65535)` — a ~2 MB reservation — and enters a 65,535-iteration `read_G` loop over a `HashMap`, all triggered by <64 bytes of input. Repeating the parse in a loop multiplies the transient footprint arbitrarily; each iteration needs no more input than the header to force the allocation.

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
