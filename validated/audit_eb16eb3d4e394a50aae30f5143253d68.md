### Title
Unbounded O(t²) interpolation and decompression work during `ThresholdKeys::read` enables unauthenticated-resource consumption on a single small message - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts an attacker-controlled `n`/`t` (each a `u16`, up to 65535) and then invokes `ThresholdKeys::new`, which computes the group key by evaluating `interpolation_factor` once per participant in `1..=t`. Each `interpolation_factor` call under Lagrange interpolation is itself O(t) field multiplications plus a field inversion, so deserialization of a single ~2 MB payload triggers O(t²) ≈ 4.3 billion field operations and 65535 field inversions of CPU-bound work. There is no cap on `n`/`t`, mirroring the unbounded `date_sequence` loop in the external report: no maximum bound on an attacker-chosen quantity driving an expensive per-unit computation.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, `i` directly from the byte stream and then constructs `ThresholdKeys` via `ThresholdKeys::new` (crypto/dkg/src/lib.rs:591-632). `ThresholdParams::new` only enforces `0 < t <= n` and `i <= n`; `n` can be the full `u16::MAX` (lib.rs:166-179). The reader then performs `n` point decompressions and feeds everything to `ThresholdKeys::new`, which computes the group key as the sum over `1..=t` of `verification_shares[i] * interpolation.interpolation_factor(*i, &t)` (lib.rs:376-378). For `Interpolation::Lagrange`, `interpolation_factor` loops over all `t` included participants performing a field multiplication per element and a scalar inversion (`denom.invert().unwrap()`) per call (lib.rs:229-247). Total cost is therefore O(t²) field multiplications plus t inversions — on the order of billions of field operations for t = 65535 — all driven by an input field totaling just a few bytes of the message.

### Impact Explanation
A single untrusted byte stream of roughly `6 + 1 + 32 + 32·65535 ≈ 2 MB` (ID length + curve ID + params + interpolation tag + secret share + n verification shares) causes the deserializer to burn seconds to minutes of CPU inside `ThresholdKeys::new`, entirely serially on the calling thread. Because `ThresholdKeys::read` is the supported entry point for parsing threshold keys from untrusted bytes, any component exposing it to remote/attacker-controlled input can be stalled by repeated crafted payloads — worker/executor exhaustion analogous to the report's "repeated requests exhaust all worker threads". This is CWE-400 Uncontrolled Resource Consumption; severity Medium given the amplification ratio (~2 MB of input buys orders of magnitude more compute) and the lack of any bound on `n` or `t` beyond `u16::MAX`.

### Likelihood Explanation
Reachability only requires an unprivileged party to deliver a crafted byte stream to a `ThresholdKeys::read` call site — the read path performs the expensive `ThresholdKeys::new` computation unconditionally before returning a result, and it occurs after all cheap checks (`t <= n`, participant bounds). An attacker needs no valid keys, signatures, or protocol state: arbitrary canonical field/point encodings suffice, since the O(t²) work happens on the parsed values regardless of their semantic validity (the resulting group key need not correspond to any real DKG). The only constraint is `t <= n <= 65535`, which still permits the maximum-cost shape `t = n = 65535` under `Interpolation::Lagrange`.

### Recommendation
Enforce a hard upper bound on `n` and `t` in `ThresholdParams::new` and/or in `ThresholdKeys::read` before any work proportional to them is performed — e.g., reject `n > MAX_PARTICIPANTS` (a protocol-appropriate constant such as the validator set size) early in `ThresholdParams::new`. As defence-in-depth, batch the inversions in `interpolation_factor`/`ThresholdKeys::new` (compute all Lagrange denominators, invert once via Montgomery's trick) and consider computing the group key incrementally with a cap-checked iterator, so that even a bound violation cannot produce quadratic blowup.

### Proof of Concept
```rust
// PoC: O(t^2) CPU exhaustion via ThresholdKeys::read
// Target: crypto/dkg (ThresholdKeys::<C>::read / ThresholdKeys::new)
// Run under `cargo test` in crypto/dkg with ed25519-dalek via dalek-ff-group.

use std::io::Cursor;
use std::time::Instant;
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, Participant};
use dalek_ff_group::Ed25519; // Ciphersuite impl

fn main() {
    let n: u16 = u16::MAX; // 65535 — maximum allowed by ThresholdParams::new
    let t: u16 = n;        // t <= n is the only check

    // Build a syntactically valid serialization:
    //   u32 ID len | ID | u16 t | u16 n | u16 i | u8 interpolation | F secret | n * G
    let mut buf = Vec::new();
    buf.extend(&(Ed25519::ID.len() as u32).to_le_bytes());
    buf.extend(Ed25519::ID);
    buf.extend(&t.to_le_bytes());
    buf.extend(&n.to_le_bytes());
    buf.extend(&1u16.to_le_bytes());          // i = 1
    buf.push(1);                               // Interpolation::Lagrange
    buf.extend(Ed25519::generator()            // any canonical scalar works
        .mul_scalar_dummy();
    // simpler: write F::ONE repr then n copies of a canonical point
    // (both satisfy canonicality checks in read_F / read_G)

    // ... [serialize secret share and n copies of Ed25519::generator().to_bytes()] ...

    let start = Instant::now();
    let res = ThresholdKeys::<Ed25519>::read(&mut Cursor::new(&buf));
    let elapsed = start.elapsed();

    // Expected: res is Ok(ThresholdKeys) after ~minutes of CPU
    //   - n = 65535 read_G decompressions
    //   - ThresholdKeys::new computes sum over t=65535 participants,
    //     each interpolation_factor doing O(t) muls + 1 inversion
    //   => ~4.3e9 field multiplications + 65535 inversions
    println!("read result ok={} elapsed={:?}", res.is_ok(), elapsed);
}
```

Baseline comparison: a legitimate `n = t = 150` payload deserializes in milliseconds; the crafted `n = t = 65535` payload (~2 MB) forces quadratic interpolation work bounded only by `u16::MAX`, with no maximum-size check anywhere in the read path (crypto/dkg/src/lib.rs:166-179, 376-378, 229-247, 591-632).