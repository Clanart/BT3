### Title
Unbounded length-prefixed deserialization loop in `SchnorrAggregate::read` enables CPU-exhaustion / hang denial of service - (File: crypto/schnorr/src/aggregate.rs)

### Summary
CVE-2018-5786 describes an infinite loop / application hang in lrzip's `get_fileinfo`, where a crafted input file makes the parser loop indefinitely — a bug class of *untrusted length/loop-bound fields driving deserialization loops with no sanity cap*. The analogous shape in Serai is `SchnorrAggregate::read`, which reads an attacker-controlled `u32` element count and then iterates that many times performing full group-element decompression (`C::read_G`), with no upper bound on the declared count and no budget on total work.

### Finding Description
`SchnorrAggregate::read` (crypto/schnorr/src/aggregate.rs:77-88) parses a serialized aggregate Schnorr signature:

```rust
let mut len = [0; 4];
reader.read_exact(&mut len)?;
let mut Rs = vec![];
for _ in 0 .. u32::from_le_bytes(len) {
  Rs.push(C::read_G(reader)?);
}
Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
```

The loop bound is a raw `u32` read directly from the input — up to 4,294,967,295 iterations. Each iteration calls `C::read_G`, which performs full point decoding (field inversion, square-root/legendre checks, torsion/subgroup validation for the dalek-ff-group curves) and pushes into an ever-growing `Vec`. There is no equivalent of the `TRANSACTION_SIZE_LIMIT` / `MAX_LIBP2P_REQRES_MESSAGE_SIZE` guards used elsewhere in the codebase (contrast `coordinator/src/p2p.rs` length cap and `coordinator/src/tributary/transaction.rs` checking `commitments_len * each_commitments_len > TRANSACTION_SIZE_LIMIT` before looping). `SchnorrAggregate` exposes `read`/`serialize` as a public wire format, so any protocol path that reads an aggregate signature supplied by another party (coordinator tributary transactions, processor message exchange, RPC-fed verification) will happily spin on the attacker's declared count. Because the reader is generic `io::Read`, a peer that streams bytes slowly (or simply supplies a large blob) keeps the victim in the decompress-and-push loop for as long as the attacker chooses, pinning a thread and growing memory linearly — the same "crafted input causes unbounded parser work" primitive as the lrzip hang.

### Impact Explanation
An unprivileged party who can get the victim to deserialize a `SchnorrAggregate` they control declares `len = u32::MAX` and supplies a byte stream sized to their patience. The victim performs up to ~4 billion point decompressions and unbounded `Vec` growth inside a single `read` call, with no early-abort other than stream exhaustion. In an async executor context this blocks the worker thread; in any context it is an unauthenticated CPU/memory denial of service — the direct analog of lrzip's application hang via crafted file, mapped onto the signature-deserialization path rather than a file-format header.

### Likelihood Explanation
The function is `pub`, operates on a generic `io::Read`, and is the deserialization entry point for an on-wire signature type — exactly the class of function (`read_*` on untrusted bytes) flagged in scope. Exploitation requires only that some caller feeds attacker-controlled bytes into `SchnorrAggregate::read` (aggregate signatures are precisely designed to be exchanged between participants). No key material, no valid signature, and no protocol position is needed — just a 4-byte length prefix of `0xffffffff` followed by a stream of syntactically valid point encodings. Confidence in the code-level bug (uncapped loop bound) is high; the residual uncertainty is which specific production caller exposes it to peers, since the call sites are outside the crypto crates.

### Recommendation
Mirror the pattern already used in `coordinator/src/tributary/transaction.rs`: before looping, check `u32::from_le_bytes(len)` against a protocol-defined maximum (e.g., maximum expected aggregate size, or enforce that the remaining buffer length is at least `len * C::G::Repr::default().as_ref().len()` bytes before allocating/iterating). For `&[u8]`-backed readers, compute `reader.len() / point_len` and reject the declared count outright if it exceeds what could possibly be present, converting the attack into an immediate `Err`.

### Proof of Concept
```rust
// crypto/schnorr aggregate deserialization, attacker-supplied bytes
use ciphersuite::Secp256k1;
use schnorr::SchnorrAggregate;

// A stream that yields valid encodings for as long as we choose,
// prefixed with a huge declared count.
let mut bytes = vec![];
bytes.extend(u32::MAX.to_le_bytes());           // 4,294,967,295 declared Rs
// Append as many valid point encodings as we want to burn the victim's CPU;
// each iteration performs full decompression + subgroup checks.
for _ in 0 .. 1_000_000 {
    bytes.extend(Secp256k1::generator().to_bytes().as_ref()); // valid G encodings
}
// Victim call: spins through every supplied point and only stops at
// stream exhaustion — proportional to attacker-provided input, up to 4 Gi points.
let _ = SchnorrAggregate::<Secp256k1>::read(&mut bytes.as_slice());
```
With a generic `io::Read` backed by a network socket rather than a slice, the attacker simply keeps feeding bytes and the loop never terminates — the lrzip-style hang transplanted into signature deserialization.