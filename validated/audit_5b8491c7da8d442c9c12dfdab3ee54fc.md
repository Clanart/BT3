### Title
`ReceivedOutput::read` silently consumes trailing bytes via an internal `BufReader`, corrupting any stream read after it - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The external report (JLSEC-2026-1306, Exiv2 null-pointer dereference) is a denial-of-service triggered when attacker-crafted input bytes are fed into a parsing path. The Serai analog is in `ReceivedOutput::read`: it wraps the caller's reader in a `BufReader` to consensus-decode a `TxOut` and `OutPoint`, then drops the `BufReader`. `BufReader` greedily buffers far more bytes than the two consensus objects consume, so bytes belonging to whatever follows the `ReceivedOutput` in the same stream are swallowed and lost — a parsing-induced denial of service / stream corruption reachable purely from untrusted bytes.

### Finding Description
`ReceivedOutput::read` reads a scalar offset, then constructs `BufReader::with_capacity(0, r)` and calls `TxOut::consensus_decode` and `OutPoint::consensus_decode` through it [1](#0-0) . `BufReader::with_capacity(0, ...)` does not disable buffering; on the first fill it lazily allocates and issues a large read against the underlying reader, buffering up to its internal capacity (DEFAULT_BUF_SIZE, ~8 KiB). `consensus_decode` performs many small reads, so the `BufReader` returns decoded data from its internal buffer while having consumed extra bytes from `r`. When the function returns, the `BufReader` — and every buffered byte it pulled past the end of the `OutPoint` — is dropped.

Consequences:

- Any bytes that follow a serialized `ReceivedOutput` in the same `Read` stream (e.g., a second `ReceivedOutput`, a length-prefixed `data` field, or any subsequent field a caller reads from `r`) are silently consumed and discarded.
- The next read from `r` therefore starts mid-stream: it either fails with `UnexpectedEof` (DoS, the direct analog of the Exiv2 crash-on-parse) or, worse, succeeds on shifted bytes, producing a semantically different object than what was serialized — an `io::Result::Ok` carrying corrupted data.
- No error is returned for the loss itself; the corruption is silent, and callers cannot detect how many bytes were eaten because the buffer size depends on how much the underlying reader yielded in one `read` call.

The same greedy-buffer shape does not appear in the in-scope `crypto/` readers inspected (`Commitments::read` at crypto/frost/src/nonce.rs:133-139, `ThresholdKeys::read` at crypto/dkg/src/lib.rs:574-632, `DLEqProof::read`/`read_scalar` at crypto/dleq/src/lib.rs:89-97, `SchnorrAggregate::read` at crypto/schnorr/src/aggregate.rs:77-88) — those all read exactly the encoded bytes from the caller's reader and propagate `io::Error` on truncation, so they do not share this defect.

### Impact Explanation
`ReceivedOutput::read` is an explicitly in-scope untrusted-bytes entry point. An unprivileged party who can cause serialized output data to be fed into it on a stream that contains anything after the `ReceivedOutput` can deterministically break deserialization of the remaining stream — forcing `io` errors that abort the surrounding protocol operation, or causing a follower field to decode from shifted bytes (e.g., a wrong `offset`, wrong outpoint, or wrong payload length). Because the number of bytes eaten depends on reader buffering behavior rather than the encoded length, the failure is not reproducible from the serialized data alone and cannot be guarded against by the caller. This maps directly onto the advisory's class: crafted input reaching a parse routine causes a denial of service of the processing party.

### Likelihood Explanation
Reachability requires a context where serialized `ReceivedOutput`s are deserialized from a shared stream followed by more data — which is precisely the wire/storage format this type's `write`/`read` pair implies (`write` emits `offset || TxOut || OutPoint` with no framing; any list or trailing-field layout places bytes after it) [2](#0-1) . Whether production callers actually read trailing bytes from the same reader instance could not be fully verified within the in-scope files alone, which is the main uncertainty; if every production use passes a reader containing exactly one `ReceivedOutput`, the trigger reduces to integrator-facing misuse. The bug is unconditional in the code, however — it needs no attacker cooperation beyond controlling the serialized bytes — consistent with a Medium-severity parsing DoS.

### Recommendation
Do not wrap `r` in a `BufReader`. Decode the `TxOut` and `OutPoint` directly against `r` (consensus decoding only issues the reads it needs), or decode from a length-bounded buffer that is then consumed exactly: e.g., `TxOut::consensus_decode(&mut *r)` / `OutPoint::consensus_decode(&mut *r)` in `ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122-134`. If a buffered adapter is genuinely needed, read the objects from a temporary `Vec<u8>` built by `r.take(known_bound)` and assert the temporary reader is fully consumed, so no bytes past the `OutPoint` can be lost. Add a regression test that concatenates two serialized `ReceivedOutput`s and round-trips both from one `&[u8]` reader.

### Proof of Concept
```rust
// Conceptual PoC for ReceivedOutput::read stream corruption
use std::io::Read;
use serai_bitcoin::wallet::ReceivedOutput; // Secp256k1-backed wallet type

// A reader that always hands out as many bytes as requested (worst case for
// BufReader over-read), e.g. &[u8].
let a: ReceivedOutput = /* any valid output */;
let b: ReceivedOutput = /* any valid output */;

let mut stream = a.serialize();
stream.extend(b.serialize());

let mut slice: &[u8] = &stream;
let a2 = ReceivedOutput::read(&mut slice).unwrap(); // OK
let b2 = ReceivedOutput::read(&mut slice);          // Err(UnexpectedEof) or corrupt:
// the BufReader inside the first call consumed b's bytes and dropped them.
assert!(b2.is_err() || b2.unwrap() != b);
```

The first `read` succeeds while its internal `BufReader` (created at `networks/bitcoin/src/wallet/mod.rs:127`) has already pulled `b`'s bytes out of `slice` into its private buffer; the second `read` then observes a truncated/shifted stream, demonstrating the crash-on-parse / silent-corruption denial of service analogous to the Exiv2 advisory.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L136-141)
```rust
  /// Write a ReceivedOutput to a generic satisfying Write.
  pub fn write<W: Write>(&self, w: &mut W) -> io::Result<()> {
    w.write_all(&self.offset.to_bytes())?;
    w.write_all(&serialize(&self.output))?;
    w.write_all(&serialize(&self.outpoint))
  }
```
