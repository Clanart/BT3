### Title
Attacker-controlled participant count in `ThresholdKeys::read` causes quadratic Lagrange interpolation and unbounded point deserialization, enabling CPU/memory exhaustion (partial DoS) - (File: crypto/dkg/src/lib.rs)

### Summary
CVE-2021-35556 is a deserialization-driven partial denial of service: untrusted input causes disproportionate resource consumption in a parser. The Serai analog is `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632), which takes a fully attacker-controlled `n` (`u16`, up to 65,535) directly from the byte stream and then (a) performs `n` field-element reads, (b) performs `n` elliptic-curve point decompressions (`read_G`), and (c) calls `ThresholdKeys::new`, which computes the group key via Lagrange interpolation over `t` participants — an O(t²) field-operation loop. There is no bound on `t`/`n` other than `t <= n <= u16::MAX`, so a small serialized blob forces seconds-to-minutes of CPU work and multi-megabyte allocations.

### Finding Description
`ThresholdKeys::read` reads `t`, `n`, and `i` as raw little-endian `u16`s from the reader at crypto/dkg/src/lib.rs:591-602. `n` is never checked against any protocol limit; it is only required to satisfy `t <= n` and `i <= n` inside `ThresholdParams::new` (crypto/dkg/src/lib.rs:166-179). The read path then:

1. For `Interpolation::Constant`, allocates `Vec::with_capacity(n)` and reads `n` scalars via `C::read_F` (crypto/dkg/src/lib.rs:607-613).
2. For all interpolation modes, loops `for l in (1..=n).map(Participant)` calling `C::read_G` (crypto/dkg/src/lib.rs:620-623) — a full point decompression/validation per supplied encoding.
3. Calls `ThresholdKeys::new`, which computes `group_key` as `t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum()` (crypto/dkg/src/lib.rs:376-378).

For `Interpolation::Lagrange`, `interpolation_factor` (crypto/dkg/src/lib.rs:229-247) iterates the entire `included` vector of length `t` for every one of the `t` participants, yielding O(t²) field multiplications plus an inversion per participant. With `t = n = 65,535` (allowed, since `ThresholdParams::new` only requires `t <= n`), this is ~4.3 billion field multiplications plus 65,535 inversions and 65,535 point decompressions, triggered by a serialized blob of only ~4–5 MB. The amplification factor (bytes-in to CPU-out) is quadratic in the attacker-chosen parameter.

### Impact Explanation
Any component that feeds untrusted bytes to `ThresholdKeys::read` — the API is part of the reachable deserialization surface — lets an unauthenticated party burn CPU proportional to the square of a two-byte field and allocate vectors/HashMaps sized by that field. A handful of crafted messages can stall a validator's signing/key-loading path (partial denial of service), matching the "unauthorized ability to cause a partial DOS" impact of CVE-2021-35556. No secret material is required and no malformed cryptography is involved: every byte can decode to a valid scalar/point; the exhaustion comes purely from the unbounded count.

### Likelihood Explanation
Likelihood depends on deployment: if serialized `ThresholdKeys` are ever accepted from network peers, restored from attacker-writable storage, or round-tripped through messages an outside party can influence, a single write of `t = n = 0xFFFF` with valid encodings triggers the quadratic loop. Even the linear portion alone (65,535 `read_G` decompressions from ~2 MB of input) is a meaningful amplification. The only mitigating factor is that `ThresholdKeys` are normally locally generated rather than received — which is why this maps to Medium rather than High.

### Recommendation
Enforce a hard upper bound on `t`/`n` inside `ThresholdKeys::read` before allocating (e.g., a protocol `MAX_PARTICIPANTS`, comparable to `MAX_KEY_SHARES_PER_SET` used elsewhere), and reject any serialized parameters exceeding it. Additionally, bound the total byte size read prior to loop entry (`n * (F::Repr + G::Repr)` must not exceed the remaining input length limit), and consider requiring `Interpolation::Lagrange` group-key derivation to be checked against a small cap or computed lazily.

### Proof of Concept
```rust
use std::io;
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, Participant};
use ciphersuite::group::GroupEncoding;

// C: any in-scope ciphersuite, e.g. a curve25519-dalek wrapper.
fn dos_payload<C: Ciphersuite>() -> Vec<u8> {
    let mut buf = vec![];
    // Curve ID header expected by ThresholdKeys::read
    buf.extend(u32::try_from(C::ID.len()).unwrap().to_le_bytes());
    buf.extend(C::ID);
    // t = n = 65535 (max u16), i = 1
    buf.extend(0xFFFFu16.to_le_bytes()); // t
    buf.extend(0xFFFFu16.to_le_bytes()); // n
    buf.extend(Participant::new(1).unwrap().to_bytes()); // i
    // Interpolation::Lagrange — triggers O(t^2) interpolation_factor calls
    buf.push(1);
    // secret_share: one valid scalar (identity/one must be a canonical repr;
    // use any valid F repr, e.g. serialized 1)
    let one = C::F::ONE.to_repr();
    buf.extend(one.as_ref());
    // n verification shares: n copies of the generator encoding
    for _ in 1..=0xFFFFu16 {
        buf.extend(C::generator().to_bytes().as_ref());
    }
    buf
}

// Passing this ~4-5 MB buffer to ThresholdKeys::<C>::read forces:
//  - 65,535 point decompressions in the verification_shares loop
//  - ThresholdKeys::new -> group_key: t * O(t) Lagrange factors
//    ~= 4.3e9 field multiplications + 65,535 inversions
let payload = dos_payload::<C>();
let _ = ThresholdKeys::<C>::read(&mut payload.as_slice()); // CPU exhaustion
```