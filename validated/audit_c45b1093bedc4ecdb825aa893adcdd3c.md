### Title
Attacker-controlled length prefix in `SchnorrAggregate::read` enables unbounded memory allocation / read exhaustion DoS - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::<C>::read` trusts a little-endian `u32` count supplied by the sender and then loops `u32::from_le_bytes(len)` times, pushing each decoded `C::G` into a `Vec` with no cap on the count and no correlation between the declared count and the bytes actually available. This is the same bug class as CVE-2016-7157 (unvalidated guest/peer-supplied fields in an input-processing path causing a process crash), mapped onto untrusted bytes fed to Serai's deserialization/`verify` surface.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`, `read` reads a 4-byte length and then performs `Rs.push(C::read_G(reader)?)` once per declared element:

```rust
// crypto/schnorr/src/aggregate.rs:77-88
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

There is no upper bound on `len` (up to ~4.3 billion entries) and `Rs` is grown without `Vec::with_capacity` amortized concerns being bounded — every element is pushed into memory. When `reader` is backed by a network stream or any source the attacker controls, a declared count near `u32::MAX` combined with a continuing stream of point encodings causes memory usage proportional to bytes sent, with no protocol-level limit. Contrast with the in-repo hardened readers that explicitly guard this pattern: `networks/ethereum/src/machine.rs` `Call::read` reads claimed length in 1 KB chunks with a comment acknowledging the "valid DoS" of claiming large lengths, and `coordinator/src/p2p.rs` `RrCodec::read_request` rejects lengths over `MAX_LIBP2P_REQRES_MESSAGE_SIZE`. `SchnorrAggregate::read` has neither bound.

Each `C::G` decoded point is ~32 bytes (curve-dependent), so ~137 GB of stream would be needed for true OOM by exhaustion of the count; however memory growth is still entirely attacker-driven per byte sent, and a declared huge length on a fixed buffer already inflates `Rs` to whatever fits — the vector grows to the input size regardless of the verifier's actual statement set. More practically, any embedder that wraps `SchnorrAggregate::read` around an authenticated or unauthenticated peer message (it is a `pub` API accepting `io::Read` over arbitrary bytes) inherits unbounded allocation per message.

### Impact Explanation
An unprivileged party who can feed bytes to `SchnorrAggregate::read` (any protocol message deserialized via `io::Read`, e.g. a signature-share/aggregate relayed to a verifier or coordinator) can force the host process to allocate memory and perform point decoding proportional to attacker-controlled input, up to a 4-byte-declared count of ~4.3 billion entries. This yields process crash via OOM or CPU-burn decoding millions of points — a direct denial of service, matching the severity class (Medium, availability-only) of the QEMU report.

### Likelihood Explanation
Reachability requires an embedding that pipes peer-supplied bytes into `SchnorrAggregate::read`. Within the audited scope the function is a public API of `crypto/schnorr` and the pattern (unbounded length-prefixed loop) is demonstrably the weaker reader in the tree; sibling code (`RrCodec`, `Call::read`) explicitly treats this as a real DoS vector. Likelihood is moderate: exploitation needs only a length field plus a stream, no secrets or privileged position.

### Recommendation
Enforce a maximum element count before looping — e.g., reject `len > MAX_AGGREGATE_SIZE` (the verifier can only meaningfully aggregate a bounded number of signatures), or derive the expected count from context rather than the wire. Alternatively, read in bounded chunks as `Call::read` does, so claimed-but-absent data cannot inflate `Rs` beyond bytes actually delivered.

### Proof of Concept
```rust
use std::io::Cursor;
use ciphersuite::Ciphersuite;
use schnorr::SchnorrAggregate;
// For some concrete Ciphersuite C in-scope (e.g. Ristretto):
let mut bytes = u32::MAX.to_le_bytes().to_vec();
// Append attacker-controlled point encodings ad infinitum; each accepted
// encoding grows `Rs` in memory with no protocol cap.
SchnorrAggregate::<C>::read(&mut Cursor::new(bytes));
```

Supporting code locations: `crypto/schnorr/src/aggregate.rs:77-88` (the unbounded loop), contrasted with `coordinator/src/p2p.rs:264-270` and `networks/ethereum/src/machine.rs:51-60` (bounded readers acknowledging the same DoS class).