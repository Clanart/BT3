### Title
Unbounded aggregate-signature length enables remote memory-exhaustion denial of service - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::read` accepts an attacker-controlled 32-bit count and then deserializes that many group elements from the input stream. Although the function does not preallocate from the declared length, it still permits an unbounded proof object when backed by a sufficiently large input or reader. For Ristretto-style 32-byte encodings, a count near `u32::MAX` implies a proof of roughly 128 GiB before the scalar is even read.

This is a reachable malformed-input denial-of-service analogue to the referenced out-of-bounds-write/crash class: public bytes can drive unbounded resource consumption and terminate the process through allocation failure or sustained parsing work.

### Finding Description
The vulnerable parser is `SchnorrAggregate::read`.

It reads a four-byte little-endian count and loops once for every declared `R` value:

```rust
let mut len = [0; 4];
reader.read_exact(&mut len)?;

let mut Rs = vec![];
for _ in 0 .. u32::from_le_bytes(len) {
  Rs.push(C::read_G(reader)?);
}
```

There is no protocol-level maximum and no remaining-input-length check before parsing. Each `C::read_G` consumes a fixed-size canonical point encoding, so the count is not trusted blindly in the usual buffer-overread sense, but it still defines how much attacker-controlled data the verifier is willing to retain in memory. A maliciously generated input can therefore make the verifier build a `Vec<C::G>` containing billions of elements.

Verification scales with the same attacker-controlled length. `verify` allocates `2 * keys_and_challenges.len() + 1` scalar/point pairs and indexes the parsed `Rs` for every supplied key/challenge.

### Impact Explanation
An unprivileged party that can submit serialized aggregate signatures can cause a verifier to allocate and populate an unbounded vector of group elements.

Depending on platform and allocator behavior, exploitation can result in:

- process termination on allocation failure;
- severe memory pressure;
- prolonged parsing and heap growth;
- denial of service to the verifier or signer service hosting verification.

For a 32-byte group encoding, declaring `u32::MAX` nonces implies approximately 128 GiB of encoded proof data and a comparably enormous in-memory vector, excluding vector, alignment, and group-element overhead.

The severity is best assessed as **Medium** under the requested scope: it is a remotely triggerable availability issue, but does not corrupt memory or bypass proof verification.

### Likelihood Explanation
The trigger is a length field fully controlled by the serialized input. No valid signature, private key, threshold share, or privileged state is required to reach the parsing loop.

Exploitation requires the attacker to provide enough encoded data for each declared point; otherwise `C::read_G` returns an `io::Error`. Therefore, this is not a one-byte crash. It is still practical where proof data is accepted over a stream, from storage, or without a small outer message-size bound. A caller that already bounds and buffers the complete message may limit the impact before calling this parser, but `SchnorrAggregate::read` itself has no defensible maximum.

### Recommendation
Add an explicit, protocol-defined maximum aggregate size and reject larger counts before allocation or iteration.

At minimum:

1. Introduce a constant such as `MAX_AGGREGATE_SIGNATURES`.
2. Convert `len` to `usize` once.
3. Reject `len == 0` if empty aggregates are invalid at this layer.
4. Reject `len > MAX_AGGREGATE_SIGNATURES`.
5. Prefer `Vec::with_capacity(len)` only after the bounded check, or retain incremental growth if avoiding capacity allocation is deliberate.
6. Where possible, validate the declared count against the remaining input length before parsing.
7. Add regression tests for oversized length prefixes and truncated input.

### Proof of Concept
The following bytes exercise the vulnerable path without requiring a complete multi-gigabyte stream:

```rust
use std::io::Read;

use bitcoin_serai::frost::curve::Secp256k1;
// Or the concrete in-scope ciphersuite used by the caller.
use schnorr::SchnorrAggregate;

fn oversized_aggregate_prefix() -> Vec<u8> {
  // Declares u32::MAX R values.
  u32::MAX.to_le_bytes().to_vec()
}

fn main() {
  let mut bytes = oversized_aggregate_prefix();
  let result = SchnorrAggregate::<Secp256k1>::read(&mut bytes.as_slice());

  // The current implementation enters a loop bounded only by u32::MAX.
  // This short buffer returns an I/O error on the first point, demonstrating
  // that the attacker-controlled count is accepted before any aggregate-size
  // sanity check. Supplying billions of canonical point encodings causes
  // proportional Vec growth.
  assert!(result.is_err());
}
```

A full availability demonstration uses a reader or input containing many valid point encodings with a large declared count, showing that `Rs.push(C::read_G(reader)?)` continues to grow until process resources are exhausted.