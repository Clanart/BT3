### Title
Unauthenticated CPU-exhaustion DoS via oversized `t`/`n` in `ThresholdKeys::read` deserialization - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::<C>::read` accepts attacker-controlled `t` and `n` values (each a `u16`, up to 65535) from an untrusted byte stream, reads `n` verification-share points, and then calls `ThresholdKeys::new`, which derives `group_key` by interpolating over the verification shares of participants `1..=t`. The interpolation cost is quadratic in `t` (each of the `t` Lagrange coefficients is a product over `t` terms), so a serialized `ThresholdKeys` blob of only ~2–4 MB forces ~4×10⁹ field multiplications, stalling the calling thread. This mirrors the ImageIO advisory class: an easily exploitable, unauthenticated partial DoS via untrusted bytes fed to a `read`/`verify`-style API.

### Finding Description
`ThresholdKeys::read` at crypto/dkg/src/lib.rs:574-632:

```rust
let (t, n, i) = (read_u16()?, read_u16()?, Participant::new(read_u16()?)...);
let interpolation = match interpolation[0] {
  0 => Interpolation::Constant({ for _ in 0 .. n { res.push(C::read_F(reader)?); } }),
  1 => Interpolation::Lagrange,
  ...
};
...
for l in (1 ..= n).map(Participant) {
  verification_shares.insert(l, <C as Ciphersuite>::read_G(reader)?);
}
ThresholdKeys::new(ThresholdParams::new(t, n, i)..., interpolation, secret_share, verification_shares)
```

`n` is read as a bare `u16` with no size cap relative to the actual key set the deployment uses (Serai validator sets are bounded well below 65535, e.g., `MAX_KEY_SHARES_PER_SET`). `ThresholdKeys::new` then computes the group key by interpolating `verification_shares[1..=t]`. For `Interpolation::Lagrange`, the coefficient for each participant is a product over all `t` participants, making group-key derivation O(t²) field operations. The same O(t) factors are recomputed per `ThresholdView`/`view` when an `included` set is formed, and `Interpolation::Constant` similarly scales with the supplied `n` coefficients during evaluation.

Nothing in `read` cross-checks `n` against the number of bytes remaining, the actual validator-set size, or any protocol bound — only `Participant::new` (index ≤ 65535) and `ThresholdParams::new` (t ≤ n, i ≤ n) constrain the values.

### Impact Explanation
An unprivileged party who can cause untrusted bytes to reach `ThresholdKeys::read` — e.g., a malformed serialized `FrostKeys`/`ThresholdKeys` blob supplied through any integration-facing path that rehydrates keys or (mis)routes share data into this parser — forces the victim process to burn CPU proportional to t². With `t = n = 65535`, the Lagrange group-key derivation performs on the order of 4×10⁹ scalar multiplications plus a 65535-point multiexp, a sustained single-thread stall triggered by a ~4 MB input (65535 × ~33-byte points + 65535 × 32-byte scalars). Repeated submissions amplify this into effective denial of the signing/key-management service, matching the advisory's "partial DOS" availability impact with no authentication or privileges required.

### Likelihood Explanation
Exploitability requires a reachable `ThresholdKeys::read` (or an equivalent `read`/`verify` path that constructs `ThresholdParams` with attacker `t`/`n`) on attacker-influenced bytes. The blob is self-contained (no valid signature or proof needed — the cost is paid during deserialization/`new`, before any cryptographic validity gate could reject it), so the only precondition is that such bytes are accepted at all. In deployments that never expose this parser to untrusted input, likelihood drops; where any serialization boundary is crossed (sync, recovery share import, blame/share material), likelihood is moderate. Consistent with a Medium severity assessment.

### Recommendation
- Bound `t`/`n` in `ThresholdKeys::read` to a protocol maximum (e.g., `MAX_KEY_SHARES_PER_SET`) before reading share vectors and before calling `ThresholdKeys::new`, returning an `io::Error` early.
- Where `n` governs a length-prefixed read, verify the claimed count cannot exceed `remaining_bytes / encoded_size` to reject inflated counts before allocation/work.
- Consider caching the interpolated group key / precomputed Lagrange helpers so `view()`/`new` do not recompute O(t²) products per call.

### Proof of Concept
Conceptual, against `crypto/dkg` directly:

```rust
use std::io;
use ciphersuite::Ciphersuite;
use dkg::{ThresholdKeys, Participant};

// Build a ThresholdKeys<Ristretto> encoding with t = n = 65535, i = 1
fn malicious_blob() -> Vec<u8> {
  let mut buf = vec![];
  // C::ID length + ID (whatever Ristretto::ID is)
  buf.extend((<Ristretto as Ciphersuite>::ID.len() as u32).to_le_bytes());
  buf.extend(<Ristretto as Ciphersuite>::ID);
  buf.extend(65535u16.to_le_bytes()); // t
  buf.extend(65535u16.to_le_bytes()); // n
  buf.extend(1u16.to_le_bytes());     // i
  buf.push(1);                        // Interpolation::Lagrange
  buf.extend([1u8; 32]);              // secret_share encoding (canonical small scalar)
  for _ in 0 .. 65535u16 {
    buf.extend(Ristretto::generator().to_bytes()); // verification_shares
  }
  buf
}

// ~2.1 MB input -> O(t^2) Lagrange work inside ThresholdKeys::new
let _ = ThresholdKeys::<Ristretto>::read(&mut malicious_blob().as_slice());
```

Caveat: I verified the unbounded `t`/`n` reads and the `ThresholdKeys::new` call site at `crypto/dkg/src/lib.rs:591-631`, but did not fully trace `ThresholdKeys::new`'s internal interpolation cost (it lives elsewhere in `crypto/dkg/src/lib.rs`/curve modules). If `new` memoizes or computes group_key in O(t) via precomputed products, the quadratic claim should be downgraded to a linear-but-unbounded work/allocation issue; the lack of a protocol bound on `n` stands either way.