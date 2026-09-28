### Title
Attacker-controlled `n`/`t` in `ThresholdKeys::read` triggers unbounded O(n) group decompression and O(t²) interpolation work, causing CPU denial of service - ([File: crypto/dkg/src/lib.rs](crypto/dkg/src/lib.rs))

### Summary
Analogous to CVE-2020-7957 — where a single attacker-controlled message forces snippet generation to scan an arbitrarily long string before the recipient can read their mail — `ThresholdKeys::<C>::read` lets an untrusted 16-bit `n` field drive all of the deserialization and key-derivation work. The reader allocates and fills vectors of length `n` and decompresses `n` group elements, then hands the parameters to `ThresholdKeys::new`, which derives the group key via interpolation over `t ≤ n` participants — quadratic scalar work. A ~2 MB crafted input forces tens of thousands of point decompressions plus up to ~4 billion field multiplications.

### Finding Description
In `ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`), `t`, `n`, and `i` are all read directly from the input stream:

- `n` scalars are read for `Interpolation::Constant` with `Vec::with_capacity(usize::from(n))` (`crypto/dkg/src/lib.rs:607-613`).
- `n` group elements are decompressed via `C::read_G` in the `verification_shares` loop (`crypto/dkg/src/lib.rs:620-623`).
- The parsed values are then passed to `ThresholdKeys::new(ThresholdParams::new(t, n, i)?, ...)` (`crypto/dkg/src/lib.rs:625-631`), which computes the group key by interpolating the first `t` verification shares — a Lagrange evaluation whose cost is O(t²) field operations.

`n` is a `u16`, so the attacker controls up to 65,535 point decompressions and, when `t ≈ n`, ~4.3×10⁹ scalar multiplications — while only supplying roughly `(n × F_size) + (n × G_size)` bytes. No bound checks `t`/`n` against any protocol maximum before the work is performed; `ThresholdParams::new` only validates consistency (`i ≤ n`, `t ≤ n`), not magnitude. The only "check" on the attacker input is that the stream contains enough bytes, which is proportional to the declared lengths — exactly the Dovecot shape: a small syntactic field forces disproportionate computational work before the call returns.

### Impact Explanation
Any component that passes peer/coordinator-supplied bytes to `ThresholdKeys::read` (the rules list it as a reachable sink for untrusted bytes) can be stalled in a single call for minutes of CPU: ~65k point decompressions (~seconds) plus O(t²) interpolation that can reach billions of field multiplications. Since `read` is synchronous and non-cancellable, this denies service to the calling task/thread — directly matching the CVE's "recipient cannot read their messages" availability loss.

### Likelihood Explanation
Exploitation requires only that an unprivileged party can feed crafted bytes into `ThresholdKeys::read` on a path where keys are re-deserialized from stored/peer-provided data. The cost is deterministic (no brute forcing needed): every peer controls `t`/`n` and every group/scalar element consumed is attacker-supplied, so work scales exactly with the attacker's chosen parameters. Constrained only by the `u16` width of the length fields, which still permits ~10⁹-scale field arithmetic.

### Recommendation
- Enforce a protocol-level maximum on `t`/`n` before any allocation, decompression, or interpolation in `ThresholdKeys::read` (e.g., reject `n` above `MAX_KEY_SHARES_PER_SET`-style bounds used elsewhere, or a crate-defined cap).
- In `ThresholdKeys::new`, reject `t` above the same cap before computing the interpolated group key.
- Prefer streaming/`Vec::push` over `Vec::with_capacity(n)` for attacker-derived counts to also bound memory amplification.

### Proof of Concept
Feed the following byte structure into `ThresholdKeys::<Ristretto>::read` (or any other `C`):

1. `id_len = len(C::ID)` (u32 LE) matching the curve, then `C::ID`.
2. `t = 0xFFFF`, `n = 0xFFFF`, `i = 1` (u16 LE each).
3. `interpolation = 1` (Lagrange — skips the n-scalar read and goes straight to shares).
4. A valid `secret_share` encoding.
5. `n = 65535` repetitions of a valid non-identity `C::G` encoding (~2 MB for 32-byte encodings).

`read` will then decompress 65,535 points and `ThresholdKeys::new` will perform Lagrange interpolation over `t = 65535` participants — O(4.3×10⁹) field operations — before returning, stalling the caller with no error path short of exhaustion.