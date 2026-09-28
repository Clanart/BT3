### Title
Attacker-controlled length field causes unbounded upfront heap allocation in `ThresholdKeys::read` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` in `crypto/dkg` deserializes a threshold parameter `n` directly from the input byte stream and immediately calls `Vec::with_capacity(usize::from(n))` to back the `Interpolation::Constant` vector, and then performs `n` scalar reads plus `n` `read_G` point deserializations, all before `ThresholdParams::new` ever validates `n` against `t`/`i`. An attacker who can feed untrusted bytes to `ThresholdKeys::read` can claim `n = 65535` in a handful of input bytes and force an immediate ~2 MiB allocation (65535 × 32-byte scalar) per call, plus up to 65535 expensive group-deserialization operations — an asymmetric resource-consumption primitive analogous to CVE-2024-3569 (a small crafted input driving disproportionate server-side work).

### Finding Description [1](#0-0) 

The `read` routine:

1. Reads `t`, `n`, `i` as raw `u16`s from the attacker-controlled reader (lines 591–602). `Participant::new` only enforces nonzero — `n` can be any value up to 65535.
2. If the interpolation tag is `0` (`Constant`), executes `Vec::with_capacity(usize::from(n))` (line 608), allocating `n * size_of::<C::F>()` bytes (~2 MiB for 32-byte fields) before a single payload byte is read.
3. Reads `n` scalars via `C::read_F` (lines 609–611).
4. Reads `n` group elements via `C::read_G` into a `HashMap` keyed by `Participant` (lines 620–623). Each `read_G` performs a full point decompression plus a canonicality re-encoding check ( [2](#0-1) ), which is comparatively expensive (inversion/sqrt per point).
5. Only at the very end does `ThresholdParams::new(t, n, i)` check `t`/`n`/`i` consistency (line 626) — after all resources have been consumed.

The amplification: ~10 bytes of input (`tag`, `t`, `n`, `i`) trigger a 2 MiB allocation; each 32–33 bytes of input additionally trigger a full point decompression. The deserialization errors out only after exhausting the claimed work, and nothing bounds the allocation by the actual remaining input length — unlike the defensive chunked-read pattern the codebase itself uses in `networks/ethereum/src/machine.rs:51-60`, which explicitly documents this exact "claim a 4 GB data is present" DoS class and mitigates it. That mitigation pattern was not applied here.

The same shape exists in the `HashMap` fill loop: a short input claiming `n = 65535` forces iterative `read_G` work until EOF, with per-iteration cost far exceeding the input bytes consumed.

### Impact Explanation
Uncontrolled resource consumption / denial of service. Any integration path where serialized `ThresholdKeys` are accepted from an untrusted source (key-share provisioning, recovery flows, or messages parsed via the `ThresholdKeys::read` sink) lets an unprivileged party force multi-megabyte transient heap allocations and tens of thousands of elliptic-curve decompression operations per request with a near-empty payload. Repeated concurrent requests amplify CPU (point decompression + canonicality checks) and allocator pressure, degrading or stalling the process — the same resource-exhaustion outcome as the referenced DoS class. Impact is bounded per call by `n ≤ 65535`, hence Medium rather than High: a single call yields ~2 MiB + ~65k decompressions, not arbitrary memory.

### Likelihood Explanation
Reachability depends on an integrator feeding externally supplied bytes into `ThresholdKeys::read`; the scan rules designate `ThresholdKeys::read` as an accepted untrusted-bytes sink, so the path is considered reachable. Exploitation requires only controlling the `n` field and the interpolation tag — no valid key material, signatures, or protocol state needed, since the allocation and parsing all occur before `ThresholdParams::new` validation. Probability of encountering such a path is moderate: `ThresholdKeys` are primarily node-local secrets, but DKG recovery/import flows are plausible ingestion points. Caveat: I could not exhaustively enumerate all call sites of `ThresholdKeys::read` in this session, so exact network exposure is unverified.

### Recommendation
Validate before allocating in `ThresholdKeys::read` (`crypto/dkg/src/lib.rs`):
- Reorder so `ThresholdParams::new(t, n, i)` consistency checks (e.g., `t <= n`, `i <= n`) run immediately after reading `t`/`n`/`i`, before any allocation or element reads.
- Impose a hard cap on `n` (e.g., the protocol's documented maximum participant count) prior to `Vec::with_capacity`.
- Avoid `with_capacity(n)` entirely or bound it to a small constant and grow on demand, so allocation tracks bytes actually present — mirroring the chunked-read defense already used in `networks/ethereum/src/machine.rs`.
- Apply the same scrutiny to sibling deserializers (`Commitments::read`, `EncryptedMessage::read`, `DLEqProof::read`, `SchnorrAggregate::read`) for length-prefixed loops that trust attacker-declared counts.

### Proof of Concept
```rust
use std::io::Cursor;
use ciphersuite::Ciphersuite;
use dalek_ff_group::Ed25519;
use dkg::ThresholdKeys;

// Byte stream claiming: id_len, curve ID, t=1, n=65535, i=1,
// interpolation = 0 (Constant) -- with NO scalar payload following.
let mut buf = vec![];
buf.extend_from_slice(&(Ed25519::ID.len() as u32).to_le_bytes());
buf.extend_from_slice(Ed25519::ID);
buf.extend_from_slice(&1u16.to_le_bytes());      // t
buf.extend_from_slice(&u16::MAX.to_le_bytes());  // n = 65535
buf.extend_from_slice(&1u16.to_le_bytes());      // i
buf.push(0);                                     // Interpolation::Constant

// Forces Vec::with_capacity(65535) (~2 MiB for 32-byte scalars)
// before attempting any scalar reads.
let _ = ThresholdKeys::<Ed25519>::read(&mut Cursor::new(buf));
```

For the CPU-amplification variant, append a valid 32-byte scalar followed by repeated valid compressed points; the parser performs one `read_G` decompression + canonicality re-check per claimed participant until EOF, with `n` capped only by `u16::MAX`.

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

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```
