### Title
Quadratic Lagrange interpolation in `ThresholdKeys::read`/`ThresholdKeys::new` enables CPU denial-of-service from a small serialized blob - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` accepts attacker-controlled `t`/`n`/`i` parameters (all `u16`, so up to 65535) and then calls `ThresholdKeys::new`, which computes the group key via Lagrange interpolation over participants `1..=t`. `Interpolation::interpolation_factor` is O(t) per participant and is invoked t times, giving O(t²) field multiplications. With `t = n = 65535`, a ~2 MB untrusted input triggers ~4.3×10⁹ field multiplications plus 65535 scalar multiplications — freezing the caller, the exact bug class as the schema-inspector ReDoS (small public input → unbounded CPU).

### Finding Description
- `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-631) reads `t`, `n`, `i` from the wire, then reads `n` field elements (Constant interpolation) or none (Lagrange), one secret share, and `n` verification shares — all sized purely by attacker bytes.
- `ThresholdParams::new` (crypto/dkg/src/lib.rs:166-179) only enforces `t, n != 0`, `t <= n`, `i <= n`. No upper bound below `u16::MAX`.
- `ThresholdKeys::new` (crypto/dkg/src/lib.rs:376-378) computes `group_key = Σ_{i∈1..=t} verification_shares[i] * interpolation_factor(i, t)`.
- `interpolation_factor` (crypto/dkg/src/lib.rs:226-247) loops over all `t` included participants performing two field multiplications and one inversion per call → total O(t²) ≈ 4.3 billion field ops at t = 65535, executed inside `read` before any error can be returned.

The same quadratic cost is reachable in `ThresholdKeys::view` (lib.rs:501-506), but `read` is the directly attacker-reachable path since `ThresholdKeys::read` is an advertised untrusted-bytes sink.

### Impact Explanation
Any service that deserializes `ThresholdKeys` (or `FrostKeys`) from network/peer-supplied bytes — e.g. tributary `Transaction` payloads, processor message handling, or reshare/recovery flows — can be halted by a ~2 MB blob encoding `t = n = 0xFFFF` with Lagrange interpolation. The thread burns CPU in a single `ThresholdKeys::new` call; per the repository's panic-kill philosophy this is a liveness failure of the node, analogous to the frozen browser/process in GHSA-f38p-c2gq-4pmr. An unprivileged party only needs to feed bytes to a `read`/`verify`-adjacent path — no key material required.

### Likelihood Explanation
Requires a code path where an external party can supply a serialized `ThresholdKeys`/`FrostKeys` blob. Within the FROST/DKG machine set, key material is exchanged during DKG, reshare, and recovery ceremonies; any deserialization of unvalidated `ThresholdKeys` bytes exposes this. The cost factor is ~65535× amplification of input size to CPU work, so even occasional reachability is a practical DoS.

### Recommendation
- Enforce a sane maximum for `t`/`n` in `ThresholdParams::new` or at the top of `ThresholdKeys::read` (e.g. the protocol's real maximum participant count), rejecting larger values before allocation/interpolation.
- Alternatively, compute the group key incrementally (O(t) total Lagrange via prefix/suffix products) rather than O(t²) naive interpolation.
- Cap the number of verification shares read before validating `params`.

### Proof of Concept
```rust
// Construct a ~2.1 MB blob that ThresholdKeys::<Ristretto>::read accepts
// and that spends ~4.3e9 field multiplications in ThresholdKeys::new.
let mut buf = vec![];
// curve ID for Ristretto (whatever C::ID is)
buf.extend_from_slice(&(Ristretto::ID.len() as u32).to_le_bytes());
buf.extend_from_slice(Ristretto::ID);
// t = 65535, n = 65535, i = 1
buf.extend_from_slice(&u16::MAX.to_le_bytes()); // t
buf.extend_from_slice(&u16::MAX.to_le_bytes()); // n
buf.extend_from_slice(&1u16.to_le_bytes());     // i
buf.push(1);                                    // Interpolation::Lagrange
buf.extend_from_slice(&[0u8; 32]);              // secret_share (valid repr: zero)
// n verification shares: repeat a valid compressed point
let p = (Ristretto::generator()).to_bytes();
for _ in 0 .. u16::MAX { buf.extend_from_slice(p.as_ref()); }

// The following performs 65535 point reads, then O(t^2) interpolation.
let _keys = ThresholdKeys::<Ristretto>::read(&mut &buf[..]); // hangs
```
Root cause confirmed at crypto/dkg/src/lib.rs:226-247 (`interpolation_factor` O(t)), :376-378 (called t times inside `ThresholdKeys::new`), and :591-631 (unbounded `t`/`n` accepted from wire in `ThresholdKeys::read`).