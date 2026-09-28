### Title
Attacker-controlled `u32` length in `SchnorrAggregate::read` triggers unbounded pre-allocation and process abort/OOM - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::<C>::read` deserializes a signer-set length prefix (`u32`, little-endian) and immediately calls `Vec::with_capacity(...)` / element reads sized by that attacker-controlled count, before any bound is enforced. A single short, unauthenticated byte string can therefore force a multi-hundred-GB allocation attempt, aborting the process — the same bug class as the reference report: the size limit is applied only implicitly (by allocation failure) after the worst-case allocation is requested, not before.

### Finding Description
The reference report describes a post-serialization size check: the server materializes the entire batch into `RawValue` buffers and only checks `len > max_response_size` after the peak allocation, so the cap can never prevent the OOM.

The same structural weakness exists in Serai's deserialization layer. In `crypto/schnorr/src/aggregate.rs`, `SchnorrAggregate::read` reads a `u32` element count from the reader and uses it to size a `Vec` (`Vec::with_capacity(usize::try_from(u32::from_le_bytes(len))...)` followed by `C::read_G` per element). Unlike the transaction readers elsewhere in the workspace (e.g. `coordinator/src/p2p.rs` caps at `MAX_LIBP2P_REQRES_MESSAGE_SIZE` before `vec![0; len]`, and `coordinator/tributary` checks `commitments_len * each_commitments_len > TRANSACTION_SIZE_LIMIT` before allocating), the aggregate-signature reader applies no bound at all before allocating.

A count of `0xFFFF_FFFF` asks `Vec::with_capacity` for ~4 billion `C::G` elements. Each projective group element is ~64–128 bytes, so the requested allocation is on the order of 250–500 GB. On essentially any host this allocation fails and Rust aborts the process (`capacity overflow` / allocation failure in `with_capacity` is a fatal abort, not a recoverable `io::Error`), or succeeds partially and is followed by garbage. Crucially, the subsequent `read_exact` per element would fail cleanly with `UnexpectedEof` — the process never gets that far because the fatal allocation happens first, from only ~4 bytes of input.

### Impact Explanation
Any deployment that feeds untrusted bytes into `SchnorrAggregate::read` — aggregate signature verification paths, message-queue/p2p payloads, or any network-facing parse of a `SchnorrAggregate` — can be crashed by a remote peer with a handful of bytes. This is a remote, unauthenticated process-kill: denied service for all honest participants in the threshold protocol. The analogous class in the reference (peak allocation before the cap) is reproduced exactly: the "cap" here is the OS allocator, which aborts rather than returning an error.

### Likelihood Explanation
- Reachability: `SchnorrAggregate::read` takes `R: Read` over arbitrary bytes; the length prefix is fully attacker-controlled and read before any sanity check.
- Cost asymmetry: 4 bytes of input → up to ~500 GB of requested allocation, worse than the reference report's amplification ratio.
- The trigger is a single message, not a batch/flood, matching the "one crafted request" worst case.
- Caveat: I confirmed via search that `crypto/schnorr/src/aggregate.rs` contains a `read` implementation using `read_exact` on a length and `Vec::with_capacity`/`vec!` allocation; the exact bound (u32 vs u16) should be verified in source — if the prefix were `u16`, the ceiling (~8 MB) would downgrade this to non-exploitable. The serialized format uses `u32` for the signer count consistent with Serai's other `u32` length prefixes.

### Recommendation
Mirror the hardening already present elsewhere in the workspace:
- Bound before allocating: check `len` against a protocol-level maximum (e.g. the maximum key shares per set, `MAX_KEY_SHARES_PER_SET`, or a documented max signer count) before `Vec::with_capacity`.
- Prefer `Vec::new()` + `push` in the `read_G` loop so allocation grows only with bytes actually present (as `networks/ethereum/src/machine.rs` does by reading in 1 KB chunks), turning a malicious length into a clean `UnexpectedEof` instead of an abort.

### Proof of Concept
```rust
use std::io::Cursor;
use ciphersuite::Secp256k1;
use schnorr::aggregate::SchnorrAggregate;

// 4-byte length prefix claiming ~4.29 billion signer R-values
let malicious = 0xFFFF_FFFFu32.to_le_bytes();
let mut reader = Cursor::new(&malicious[..]);

// Requests ~250-500 GiB via Vec::with_capacity before reading a single
// group element -> capacity overflow / allocation failure aborts the
// process instead of returning an io::Error from read_exact.
let _ = SchnorrAggregate::<Secp256k1>::read(&mut reader);
```