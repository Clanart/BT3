### Title
Attacker-controlled `n` in `ThresholdKeys::read` triggers disproportionate heap allocation and iteration before any bounds check - ([File: crypto/dkg/src/lib.rs])

### Summary
`ThresholdKeys::<C>::read` (in scope: `crypto/dkg`) reads `t`, `n`, and `i` as raw `u16`s from the input stream and immediately uses the attacker-controlled `n` to (a) pre-allocate `Vec::with_capacity(usize::from(n))` of field elements for `Interpolation::Constant`, and (b) bound two deserialization loops (`0 .. n` scalars and `1 ..= n` verification-share group elements) — all *before* `ThresholdParams::new` validates the parameters at the end of the function. A two-byte value of `0xFFFF` in a ~10-byte input forces a ~2 MB allocation (64-bit scalar like ed448 doubles this to ~4 MB) plus 65 535 attempted element reads, mirroring CVE-2021-36090's "tiny input → outsized memory" class. [1](#0-0) 

### Finding Description
In `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574`):

1. `t`, `n`, `i` are read as `u16` with no early bounds check — `Participant::new` only rejects `i == 0`. [2](#0-1) 
2. If the interpolation tag is `0` (`Constant`), `Vec::with_capacity(usize::from(n))` reserves `n * size_of::<C::F>()` bytes up front — up to 65 535 elements — driven entirely by a 2-byte field, regardless of how many bytes the input actually contains. [3](#0-2) 
3. The verification-share loop `for l in (1 ..= n)` calls `read_G` up to 65 535 times. [4](#0-3) 
4. Only afterward does `ThresholdParams::new(t, n, i)` check `t <= n` and related invariants — the damage (allocation, CPU) is already done. [5](#0-4) 

There is no consistency check between `n` and the remaining input length, and no cap tied to the protocol's real participant bound. The same pattern exists in `Commitments::read` (pedpop) only via caller-supplied `params.t()`, which is trusted — the `ThresholdKeys::read` path is the one where the count comes straight from the wire.

### Impact Explanation
An unprivileged party that can supply bytes later fed to `ThresholdKeys::read` (key-share blobs, recovery/reshare material, or any integration reading threshold keys from peer-supplied data) can force ~2–4 MB of heap allocation per ~10 bytes of input, plus up to 65 535 fallible group-element decodes per message. Repeated submissions cause memory exhaustion / OOM of the signing or coordination process — a denial of service against threshold signing availability, which for Serai directly blocks fund movement. This is the exact zip-bomb/length-inconsistency class of the reference advisory.

### Likelihood Explanation
Reachability is conditional on an integrator feeding attacker-influenced bytes to `ThresholdKeys::read`, but that is a documented input path for this API and the prompt's allowed sink list. The trigger requires only setting interpolation byte `0` and `n = 0xFFFF`; no valid signatures or protocol participation is needed since the allocation precedes all validation.

### Recommendation
- Validate `t`, `n`, `i` via `ThresholdParams::new` (or an explicit `n <= MAX_PARTICIPANTS` check) immediately after reading them, before any allocation or loop that uses `n`.
- Replace `Vec::with_capacity(n)` with incremental `push`/`Vec::new()`, or cap `n` to the maximum legal participant count, so allocation cannot exceed bytes actually present.

### Proof of Concept
```rust
// crypto/dkg/src/lib.rs — ThresholdKeys::<C>::read on ~12 bytes of input
let mut serialized = vec![];
serialized.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
serialized.extend(C::ID);
serialized.extend(1u16.to_le_bytes());      // t = 1
serialized.extend(u16::MAX.to_le_bytes());  // n = 65535
serialized.extend(1u16.to_le_bytes());      // i = 1
serialized.push(0);                         // Interpolation::Constant
// => Vec::with_capacity(65535) allocates ~2 MB of C::F immediately,
//    then attempts 65535 scalar reads and 65535 group reads.
let _ = ThresholdKeys::<C>::read::<&[u8]>(&mut serialized.as_ref());
```

Caveat: I was unable to inspect `crypto/dkg/musig/src/lib.rs` and `networks/bitcoin/src/wallet/send.rs` allocation sites in detail within the iteration limit; `ThresholdKeys::read` is the strongest verified in-scope instance of this class.

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
