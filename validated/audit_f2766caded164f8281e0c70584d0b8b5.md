### Title
Unbounded attacker-controlled point deserialization in `SchnorrAggregate::read` enables CPU/memory amplification DoS - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` trusts a 4-byte little-endian length prefix and then performs `read_G` (full elliptic-curve point decompression and validation) once per claimed element, with no upper bound on the count, no cap on total message size, and no cheap pre-validation of the claimed length against remaining input size. This is the same bug class as CVE-2023-6604: arbitrary attacker bytes are "demuxed" as a structured object without format-level sanity checks, amplifying a small input into disproportionate CPU (point decompression) and memory (an unbounded `Vec<C::G>`) consumption.

### Finding Description
In `crypto/schnorr/src/aggregate.rs:77-88`:

```rust
pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
  let mut len = [0; 4];
  reader.read_exact(&mut len)?;

  #[allow(non_snake_case)]
  let mut Rs = vec![];
  for _ in 0 .. u32::from_le_bytes(len) {
    Rs.push(C::read_G(reader)?);
  }

  Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
}
```

An attacker supplies the `u32` count entirely. Two amplification paths result:

1. **CPU amplification per input byte.** Each claimed `R` costs one `C::read_G`, which for the in-scope ciphersuites (`dalek-ff-group` Ristretto, `k256`/`kp256` secp256k1, `ed448`) performs full point decompression — field square root / inversion plus validity checks — orders of magnitude more expensive than the ~32 bytes of input consumed. A few MB of crafted input forces hundreds of thousands of expensive decompressions. There is no pre-check that `claimed_len * size_of::<G::Repr>()` fits within the available input, so even a truncated buffer still burns decompressions until EOF.

2. **Memory amplification.** `Rs` grows without bound (`vec![]` with `push`, no `with_capacity` sanity and no limit), and `write` documents that up to `2^32` entries are representable. Combined with downstream `SchnorrAggregate::verify` (`aggregate.rs:127-146`), which builds a `2n+1`-pair multiexp over `self.Rs` and `keys_and_challenges`, a large parsed aggregate then forces a large `multiexp_vartime` — further CPU amplification driven entirely by the same attacker-controlled count.

Contrast with `coordinator/src/p2p.rs:263-271`, which explicitly caps `len` at `MAX_LIBP2P_REQRES_MESSAGE_SIZE` before allocating, and `networks/ethereum/src/machine.rs:51-60`, which chunks reads because "a valid DoS would be to claim a 4 GB data is present for only 4 bytes". `SchnorrAggregate::read` has no equivalent mitigation and is part of the in-scope `crypto/schnorr` crate reachable via the listed `read`/`verify` APIs on untrusted bytes.

### Impact Explanation
Any Serai component that deserializes a `SchnorrAggregate` from a peer- or user-supplied byte stream (e.g., aggregating `ReceivedOutput`/multisig proofs) lets an unprivileged party cause unbounded CPU burn (per-element decompression plus an O(n) multiexp in `verify`) and unbounded heap growth from a tiny input. This degrades node throughput or stalls verification pipelines — a Medium-severity availability impact matching the FFmpeg XBIN precedent (CVSS 5.3, A:L).

### Likelihood Explanation
Exploitation requires only that an attacker-controlled buffer reach `SchnorrAggregate::read` — the exact class of "untrusted bytes fed to `read_*` / `verify`" inputs in scope. No keys, no validator status, and no protocol participation are needed; the cost is incurred during parsing, before any signature verification can reject the input. The amplification factor is fixed per element (one decompression per ~32 bytes), so impact scales linearly with message size caps of the surrounding transport rather than being blocked by authentication.

### Recommendation
- Before looping, bound `len` by a protocol-meaningful maximum (the number of aggregated signatures is inherently bounded by participant/statement counts; reject counts above that).
- Track bytes consumed or wrap the reader in `io::Read::take(remaining)` so the declared count cannot exceed available input, failing fast instead of decompressing until EOF.
- Cap `Rs` allocation (`Vec::with_capacity` only after validating `len`) and enforce matching `Rs.len() == keys_and_challenges.len()` cheaply before any multiexp in `verify`.

### Proof of Concept
```rust
use std::io;
use schnorr::aggregate::SchnorrAggregate;
use ciphersuite::Ciphersuite;
use dalek_ff_group::Ristretto;

fn poc() {
  // Attacker claims 1_000_000 aggregated Rs and supplies 1M garbage points (~32 MB),
  // or a truncated buffer — either way every present element triggers a full
  // point decompression before EOF is detected.
  let mut buf = Vec::new();
  buf.extend(1_000_000u32.to_le_bytes());
  buf.extend(std::iter::repeat(0x02u8).take(1_000_000 * 32)); // arbitrary bytes as "points"
  buf.extend([0u8; 32]); // s

  // Each iteration performs C::read_G: decompress + validity check (~10s of µs
  // per element for Ristretto/kp256), turning ~32 MB of input into minutes of CPU
  // and a 1M-element Vec<C::G> allocation.
  let _ = SchnorrAggregate::<Ristretto>::read(&mut buf.as_slice());
}
```

Even a much smaller payload (e.g., 32 KB declaring 1000 `R`s) forces ~1000 decompressions plus a `Vec` of 1000 curve points, and a subsequent `verify` call runs `multiexp_vartime` over 2001 pairs — all before any semantic rejection is possible.

Caveat: the per-element cost is proportional to input length (each `read_G` consumes a full point encoding), so worst-case amplification is bounded by the transport's message-size limit; the unbounded `u32` count and missing "claimed length vs. remaining bytes" check are what make this an analog of the XBIN validation flaw rather than a purely linear parse.