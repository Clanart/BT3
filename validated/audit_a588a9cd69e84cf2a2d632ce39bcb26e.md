### Title
Attacker-controlled length field causes disproportionate memory allocation in `ThresholdKeys::read` - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` trusts a 2-byte attacker-supplied participant count `n` and immediately performs `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` vector, and then iterates `n` times reading field elements and group elements into a `HashMap`. A peer can declare `n = 0xffff` in a tiny message and force allocation of ~2 MB (65535 × 32-byte scalars) plus up to 131070 curve-point deserialization attempts, none of which requires the attacker to actually transmit that much data. This is the same bug class as GHSA-g35j-m5xg-vh3q: a declared-length field bounds allocation/parse work rather than the actual bytes received, and untrusted input drives retention of buffers sized by the attacker's claim.

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` reads `t`, `n`, and `i` as little-endian `u16`s directly from the reader [1](#0-0) . When the interpolation tag is `0` (Constant), it executes `Vec::with_capacity(usize::from(n))` before reading a single scalar [2](#0-1) . Independently of the tag, it then loops `for l in (1 ..= n)` inserting `C::read_G(reader)` results into `verification_shares` [3](#0-2) . Only afterward does `ThresholdParams::new(t, n, i)` validate `n` [4](#0-3) .

Consequences of this ordering:
- The `with_capacity` allocation happens unconditionally with attacker-chosen `n` up to 65535 — roughly 2 MiB committed for ed25519-sized scalars, ~30000× amplification over the 2 bytes that triggered it, before any stream data backs the claim.
- The `verification_shares` loop performs up to 65535 group-element `read_exact`/decompression attempts even when `Interpolation::Lagrange` is selected; an attacker streaming invalid point encodings forces expensive decompression per element, or early EOF — but each iteration still costs a `HashMap` entry attempt plus repr buffer work.
- The error path returns the allocation to the allocator, but each malformed message creates a fresh transient multi-megabyte allocation and long-running decompress loop, which is a resource-amplification primitive per request. There is no cap tied to the caller's actual parameter size — the real `n` in Serai deployments is a small validator-set count (≤ a few hundred), yet the decoder accepts 65535.

`ThresholdKeys::read` is explicitly in the reachable untrusted-input surface (it is the deserialization entry for keys received/restored from externally supplied bytes). `SignData::read` in `coordinator/src/tributary/transaction.rs` shows the codebase's correct pattern elsewhere — bounding data chunks by `u16` and capping piece count — but `ThresholdKeys::read` applies no such sanity bound before allocation [5](#0-4) . Similarly, `Commitments::read` in `crypto/dkg/pedpop/src/lib.rs` correctly sizes against the already-validated `params.t()` rather than a stream-supplied count [6](#0-5) .

### Impact Explanation
An unprivileged party able to feed serialized `ThresholdKeys` bytes to a node (key-share/key-blob exchange during DKG recovery, reshare, or view restoration) can force multi-megabyte allocations and tens of thousands of curve-point decompressions per message with only a handful of bytes sent. Repeated messages or a small flood of such blobs produce memory pressure / allocator churn and CPU burn disproportionate to traffic, degrading or crashing the process — an availability denial of service consistent with a Medium CWE-770-style finding. No secret material is leaked and no forgery is possible; impact is availability only, matching the reference advisory's `A:L` severity.

### Likelihood Explanation
Exploitability requires only the ability to submit attacker-controlled bytes to a `ThresholdKeys::read` caller — a public-input path per the accepted reachability model. The trigger is trivial: set `n = 0xffff` and end or pad the stream. It requires no threshold position, no valid signature, and no knowledge of secrets. The main uncertainty is how broadly integrators expose `ThresholdKeys::read` to unauthenticated input; where it is reached (e.g., any network or storage path deserializing keys before authentication), the amplification is deterministic.

### Recommendation
In `ThresholdKeys::read` (`crypto/dkg/src/lib.rs`):
1. Validate `t`, `n`, `i` by constructing `ThresholdParams::new(t, n, i)` *before* any allocation or loop, so semantically invalid counts are rejected up front.
2. Apply a hard upper bound on `n` (e.g., `u8::MAX` or the protocol's documented maximum participant count) before `Vec::with_capacity` and the `verification_shares` loop.
3. Replace `Vec::with_capacity(n)` with incremental `push` (allocation then scales with bytes actually present, as `read_exact` fails on short streams), or capacity-limit to `min(n, remaining_stream_hint)`.

### Proof of Concept
```rust
use std::io;
// Crafted blob for ThresholdKeys::<Ed25519>::read (or any Ciphersuite):
// - id_len: 4-byte LE matching C::ID.len()
// - id:     C::ID bytes
// - t: 0x0100 (t=1), n: 0xffff, i: 0x0100  <-- attacker sets n = 65535
// - interpolation tag: 0x00 (Constant)
// - stream ends here.
//
// Effect at crypto/dkg/src/lib.rs:608:
//   Vec::with_capacity(65535) allocates ~2 MiB immediately,
//   then the read_F loop errors on EOF.
// If instead interpolation tag = 0x01, the verification_shares loop at
// lines 620-623 issues 65535 read_G attempts before ThresholdParams::new
// (line 625) ever validates n.
```

Concrete byte layout (prefixed after a valid `id_len || id` header): `01 00 ff ff 01 00 00` triggers the 2 MiB `with_capacity`; `01 00 ff ff 01 00 01` triggers 65535 `read_G` calls. A single 13-byte post-header payload yields ~2 MiB allocation or ~10⁵ decompression attempts — demonstrable amplification with no authentication or valid-key prerequisite.

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

**File:** crypto/dkg/src/lib.rs (L607-613)
```rust
      0 => Interpolation::Constant({
        let mut res = Vec::with_capacity(usize::from(n));
        for _ in 0 .. n {
          res.push(C::read_F(reader)?);
        }
        res
      }),
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

**File:** coordinator/src/tributary/transaction.rs (L82-97)
```rust
    let data = {
      let mut data_pieces = [0];
      reader.read_exact(&mut data_pieces)?;
      if data_pieces[0] == 0 {
        Err(io::Error::other("zero pieces of data in SignData"))?;
      }
      let mut all_data = vec![];
      for _ in 0 .. data_pieces[0] {
        let mut data_len = [0; 2];
        reader.read_exact(&mut data_len)?;
        let mut data = vec![0; usize::from(u16::from_le_bytes(data_len))];
        reader.read_exact(&mut data)?;
        all_data.push(data);
      }
      all_data
    };
```

**File:** crypto/dkg/pedpop/src/lib.rs (L110-112)
```rust
  fn read<R: Read>(reader: &mut R, params: ThresholdParams) -> io::Result<Self> {
    let mut commitments = Vec::with_capacity(params.t().into());
    let mut cached_msg = vec![];
```
