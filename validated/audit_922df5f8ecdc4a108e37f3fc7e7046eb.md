### Title
Attacker-controlled participant count in `ThresholdKeys::read` forces immediate multi-megabyte allocation per tiny input - ([File: crypto/dkg/src/lib.rs](https://github.com/Annirich/serai--001))

### Summary
`ThresholdKeys::<C>::read` trusts the serialized `n` (u16 participant count) field and performs `Vec::with_capacity(usize::from(n))` (Constant interpolation branch) and iterates `1 ..= n` populating a `HashMap` of group elements — before validating that the claimed number of elements is actually present in the stream. A handful of input bytes therefore triggers an allocation of up to 65,535 field elements (~2 MiB) instantly, and up to 65,535 deserialization iterations. Repeated calls against any code path feeding untrusted bytes to `ThresholdKeys::read` (key-exchange / key-resharing / recovery messages between validators) yield unbounded cumulative memory consumption and denial-of-service — the same bug class as GHSA-4qhc-v8r6-8vwm (unbounded memory consumption driven by inbound request-triggering parsing).

### Finding Description
In `ThresholdKeys::read`, the `t`, `n`, and `i` u16 fields are read from the attacker-controlled reader, then:

- If `interpolation[0] == 0`, `Vec::with_capacity(usize::from(n))` allocates `n * size_of::<C::F>()` (~32 B each) immediately — up to ~2 MiB — regardless of how many bytes actually follow in the stream (`crypto/dkg/src/lib.rs`, lines 604-613).
- `verification_shares.insert(l, C::read_G(reader)?)` runs for `l in 1 ..= n` — up to 65,535 group-element deserialization attempts and HashMap insertions (lines 620-623).

There is no cap on `n` relative to the remaining input length, and no pre-check that `n` is consistent with the actual serialized size. `ThresholdParams::new` validates only `t <= n` and `i <= n` (lines 166-179), and it is called *after* the allocations/reads. The memory is consumed during parsing itself, matching the "requests triggering a check lead to unbounded memory consumption" shape: the allocation happens during untrusted deserialization, before semantic validation rejects the value.

Because the reader fails only when `read_exact` hits EOF, the Constant-interpolation `with_capacity` and the per-iteration `read_G` calls mean a truncated ~9-byte prefix already reserves the full `n`-scaled vector capacity; each such message also burns CPU on up to 65,535 point decompressions before erroring.

### Impact Explanation
- An unprivileged party able to submit serialized `ThresholdKeys` bytes (e.g., during a key-resharing/promotion/recovery round, or any message type embedding a `ThresholdKeys::read`) can force each parsing call to allocate ~2 MiB and attempt tens of thousands of curve-point decompressions from a message only a few bytes long.
- Repeated submissions cause cumulative heap growth and CPU burn without the attacker supplying proportional bandwidth — an asymmetric amplification DoS against a validator/processor, potentially aborting DKG, resharing, or signing availability. Under the codebase's own threat framing this is a node-level denial-of-service reachable purely from public, unauthenticated bytes.

### Likelihood Explanation
The path is reachable wherever `ThresholdKeys::read` consumes bytes not produced by the local honest serializer — the serialization format is self-describing (`t`, `n`, `i`, interpolation tag), so any peer-supplied key blob hits this code. `n` is a full u16, so no exotic conditions are needed: set `n = 0xFFFF`, tag = 0 (Constant), and truncate. The only mitigating factor is that callers which feed only locally-written, already-validated blobs into `read` are not exposed; exposure depends on the caller, but the library itself provides no bound, so every untrusted-input caller inherits the amplification.

### Recommendation
- In `ThresholdKeys::read`, before allocating, bound `n` by the remaining input: each participant requires a fixed-size `C::G` encoding, so require `n <= (remaining_bytes / G_repr_size)` or simply validate `ThresholdParams::new(t, n, i)` *before* reading interpolation/shares and reject `n` values inconsistent with a documented maximum set size.
- Replace `Vec::with_capacity(n)` in the `Interpolation::Constant` branch with incremental `push` (or cap at a protocol maximum such as `n <= 1024`).
- Apply the same "validate-before-allocate" ordering elsewhere: `ThresholdParams::new` should run immediately after reading `t`/`n`/`i`, and `n` should be checked against `t` (Constant requires `t == n`) before any `n`-scaled work.

### Proof of Concept
Conceptual byte stream fed to `ThresholdKeys::<Ed25519>::read(&mut bytes.as_slice())`:

```
[id_len u32 LE][ID bytes]          // matches C::ID
t  = 01 00                          // u16 = 1
n  = FF FF                          // u16 = 65535
i  = 01 00                          // Participant(1)
tag = 00                            // Interpolation::Constant
```

At line 608, `Vec::with_capacity(65535)` reserves ~2 MiB for `C::F` elements and the loop then calls `C::read_F` 65,535 times; the `verification_shares` loop at lines 620-623 would attempt 65,535 `read_G` decompressions. A truncated stream still pays the full `with_capacity` cost. Looping this input against a service that parses peer-supplied `ThresholdKeys` grows memory linearly in request count while the attacker's input stays ~15 bytes per request — an unbounded-consumption DoS mirroring the referenced advisory.