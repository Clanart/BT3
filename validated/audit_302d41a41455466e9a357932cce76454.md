### Title
Unbounded allocation in `ThresholdKeys::read` via attacker-controlled participant count — (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` deserializes a `n` value (`u16`) straight from the input and immediately allocates a `Vec` with capacity `n` (for `Interpolation::Constant` coefficients) and drives a `HashMap` insertion loop over `1 ..= n` group elements. A handful of input bytes therefore forces allocation proportional to the claimed count, before any backing bytes are verified to exist — the same unbounded-parts class as the Jenkins FileUpload DoS (CWE-770).

### Finding Description
In `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632):

- `n` is read as a raw `u16` at lines 591-602 with no sanity cap beyond `ThresholdParams::new` validation which happens *after* allocation.
- For `interpolation[0] == 0`, `Vec::with_capacity(usize::from(n))` at line 608 allocates `n * size_of::<C::F>` (up to 65535 × 32 B ≈ 2 MiB) immediately, then attempts `n` `read_F` calls — the allocation succeeds even if the stream ends right after the header.
- Independently of the interpolation tag, lines 620-623 loop `for l in (1 ..= n)` inserting into a `HashMap`, each iteration performing a `read_G` (canonical point decode). The read consumes only ~33 bytes per element, but an attacker streaming `n` crafted encodings forces `n` point-decompression operations and `n` hashmap insertions from a single unauthenticated message.

### Impact Explanation
An unprivileged remote party feeding bytes to `ThresholdKeys::read` (or any protocol message embedding it) can cause repeated multi-megabyte allocations and tens of thousands of elliptic-curve decompression operations per message with minimal bandwidth cost. Repeated across connections this exhausts memory/CPU of the verifier — a denial of service against the node, matching the availability-only impact of the original advisory.

### Likelihood Explanation
`n` is fully attacker-controlled, reaches the allocation path before `ThresholdParams::new(t, n, i)` validates it (line 626), and no upper bound (e.g., a max-set-size constant) is enforced prior to `with_capacity`. Reachability is limited to contexts where serialized `ThresholdKeys` come from untrusted input rather than local trusted storage, which lowers likelihood to Medium.

### Recommendation
- Validate `t`, `n`, `i` via `ThresholdParams::new` *before* any allocation, and enforce a hard `MAX_KEY_SHARES_PER_SET`-style cap on `n`.
- Replace `Vec::with_capacity(n)` + per-element reads with incremental `push` inside the read loop, or read into a small bounded buffer so allocation grows only with bytes actually consumed.

### Proof of Concept
```rust
use std::io::Cursor;
// For any ciphersuite C with ID of length L:
let mut buf = vec![];
buf.extend(&(C::ID.len() as u32).to_le_bytes()); // id_len
buf.extend(C::ID);                              // id
buf.extend(&0u16.to_le_bytes());                // t
buf.extend(&u16::MAX.to_le_bytes());            // n = 65535
buf.extend(&1u16.to_le_bytes());                // i
buf.push(0);                                    // Interpolation::Constant
// Buf is ~14 bytes; ThresholdKeys::read performs Vec::with_capacity(65535)
// and begins 65535 read_F iterations before erroring.
let _ = ThresholdKeys::<C>::read(&mut Cursor::new(buf));
```