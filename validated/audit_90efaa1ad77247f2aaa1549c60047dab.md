### Title
Attacker-controlled `n` in `ThresholdKeys::read` triggers large pre-validation heap allocation (memory amplification DoS) - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
`ThresholdKeys::<C>::read` reads the participant count `n` directly from the input byte stream and immediately calls `Vec::with_capacity(usize::from(n))` for the `Interpolation::Constant` vector, before reading or validating a single scalar. An attacker supplying a ~10-byte input with `n = 0xFFFF` forces an allocation of ~2 MiB (65535 × `C::F`), and the subsequent per-participant `verification_shares` loop over `1 ..= n` amplifies CPU/allocation further if bytes are provided. This mirrors the bug class of CVE-2017-12691 (memory consumption via a crafted length field in untrusted input).

### Finding Description
In `crypto/dkg/src/lib.rs`, `ThresholdKeys::read` deserializes `t`, `n`, `i` from the reader:

- Lines 591–602 read `t`, `n`, `i` as raw `u16`s with no sanity bound before use.
- Line 608 does `Vec::with_capacity(usize::from(n))` — allocating `n * size_of::<C::F>()` bytes (≈2 MiB for n=65535 on a 32-byte field) purely off an attacker-controlled field, before any element is actually read.
- Lines 620–623 then loop `for l in (1 ..= n)` inserting into a `HashMap` and calling `C::read_G(reader)` per iteration — bounded by input bytes, but each supplied point also performs a full group deserialization/decompression.
- Only at line 625 does `ThresholdParams::new(t, n, i)` validate parameters — after the allocation and (potentially) the reads have already occurred.

The allocation happens before any validation and before the stream is proven to actually contain `n` elements, so a handful of input bytes commits multi-megabyte allocations. Repeated calls (e.g., each delivered key-share/blob message routed to `ThresholdKeys::read`) multiply memory pressure linearly with request count, not with input size.

### Impact Explanation
An unprivileged party who can cause the node/processor to deserialize attacker-controlled bytes via `ThresholdKeys::read` (one of the explicitly reachable untrusted-byte sinks) can force disproportionate heap allocations — roughly 2 MiB committed per ~10 bytes of input, plus an attempted 65535-iteration point-deserialization loop when more bytes are supplied. Sustained requests exhaust memory and/or burn CPU in `read_G` decompression, denying service to the validator. This is a remote memory-consumption DoS, matching the CVE's impact (CWE-400 style uncontrolled resource consumption).

### Likelihood Explanation
Likelihood depends on whether callers feed untrusted input into `ThresholdKeys::read`. The sink is listed among the reachable deserialization entry points, `n` is a raw `u16` taken at face value, and there is no upstream length cap inside the function. The attack requires no secret material, no collusion, and minimal bandwidth; cost to the attacker is trivial while cost to the victim per message is ~65k× amplification. Uncertainty: I could not fully trace every production callsite feeding `ThresholdKeys::read` within scope, so reachability in a specific deployment path is assumed per the scope rules rather than proven end-to-end.

### Recommendation
- Validate `t`/`n`/`i` bounds (e.g., `n >= t`, `n <= MAX_PARTICIPANTS`, `i <= n`, `i != 0`) before any allocation or element reads.
- Replace `Vec::with_capacity(n)` with a bounded capacity (e.g., `min(n, SOME_HARD_CAP)`), or avoid pre-allocation for untrusted `n`.
- Check that the interpolation-tag byte is valid before allocating, and consider enforcing a maximum serialized size / element count consistent with the protocol's real participant limits (participant indexes are `u16` but actual multisigs are far smaller).

### Proof of Concept
Conceptual, using the `read` path directly:

```rust
// crypto/dkg/src/lib.rs — ThresholdKeys::read
// Crafted input for C = Secp256k1 (or any Ciphersuite):
let mut bytes = vec![];
bytes.extend((C::ID.len() as u32).to_le_bytes()); // matching ID len
bytes.extend(C::ID);                            // matching ID
bytes.extend(1u16.to_le_bytes());               // t = 1
bytes.extend(0xFFFFu16.to_le_bytes());          // n = 65535  <- attacker field
bytes.extend(1u16.to_le_bytes());               // i = 1
bytes.push(0);                                  // Interpolation::Constant
// Vec::with_capacity(65535) executes here: ~2 MiB committed
// for ~9 + ID.len() bytes of input. read_exact then fails on
// the first C::read_F, but the allocation already occurred.
let _ = ThresholdKeys::<C>::read(&mut bytes.as_slice());
```

Key supporting code: the unbounded pre-allocation at `crypto/dkg/src/lib.rs:608` (`Vec::with_capacity(usize::from(n))`), the attacker-controlled `n` read at `crypto/dkg/src/lib.rs:591-602`, and the `1 ..= n` per-participant `read_G` loop at `crypto/dkg/src/lib.rs:620-623`, all preceding parameter validation at `crypto/dkg/src/lib.rs:625-631`.

Note: I was unable to confirm the specific production entry point that pipes remote input into `ThresholdKeys::read` within the limited iteration budget; the finding stands on the in-scope sink and the missing length validation itself.