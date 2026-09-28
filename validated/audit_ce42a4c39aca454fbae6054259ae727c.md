### Title
Quadratic Lagrange interpolation during `ThresholdKeys::read` enables denial of service from untrusted bytes - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` accepts a fully attacker-controlled `(t, n)` pair with `t, n` as `u16` up to 65535, then calls `ThresholdKeys::new`, which computes the group key by evaluating a Lagrange `interpolation_factor` per participant — an O(t) inner loop of field multiplications plus a field inversion per factor. This yields O(t²) field arithmetic (~4.3×10⁹ multiplications and 65535 inversions at the maximum) triggered by parsing a ~2 MB byte blob, mirroring the CVE-2019-15961 class of inefficient parsing routines causing extreme processing times on crafted input.

### Finding Description
`ThresholdKeys::read` (`crypto/dkg/src/lib.rs:574-632`) reads `t`, `n`, and `i` as raw `u16`s from the reader, reads `n` verification-share points via `C::read_G` (line 621-623), and calls `ThresholdKeys::new` (line 625). `ThresholdParams::new` only enforces `t <= n` and `i <= n` (`crypto/dkg/src/lib.rs:166-179`); there is no upper bound beyond `u16::MAX`.

`ThresholdKeys::new` then derives `group_key` as:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

(`crypto/dkg/src/lib.rs:376-378`). For `Interpolation::Lagrange`, `interpolation_factor` iterates every other included index, multiplying `num` and `denom`, and finishing with `denom.invert().unwrap()` (`crypto/dkg/src/lib.rs:229-247`). Total cost is therefore Θ(t²) scalar multiplications plus t field inversions and t point-scalar multiplications — all inside the "parse" path.

The same quadratic behavior re-occurs in `ThresholdKeys::view` (`crypto/dkg/src/lib.rs:500-507`), which recomputes `interpolation_factor` for each of the up-to-`n` included participants on every signing operation.

### Impact Explanation
An unprivileged party who can feed crafted bytes to `ThresholdKeys::read` (a sink explicitly reachable in Serai's threat model, e.g. keys material received over the wire or from untrusted storage) can specify `t = n = 65535` with a valid `Interpolation::Lagrange` tag. Deserialization alone performs ~4.3 billion field multiplications and 65535 field inversions, pinning a CPU core for an extended period per single small input. Repeated submissions compound into a sustained denial of service of the signing/key-management process — a pure-availability impact matching the reference advisory's profile (CVSS 6.5, A:H, no confidentiality/integrity impact).

### Likelihood Explanation
Exploitation requires only delivering attacker-chosen bytes to `ThresholdKeys::read`; no signing participation, valid shares, or protocol position is needed — the expensive work happens before any semantic validation of the key material can reject it. Cost is deterministic and input-independent once `t` is chosen, so no probabilistic crafting is required.

### Recommendation
Enforce a sane protocol-level bound on `n` (and hence `t`) inside `ThresholdParams::new` or `ThresholdKeys::read` — e.g. reject `n` exceeding the maximum validator set size (`MAX_KEY_SHARES_PER_SET`) — before any interpolation work is performed. Additionally, consider computing all Lagrange denominators with a single inversion via batch inversion, and caching interpolated verification shares rather than recomputing `interpolation_factor` per signer in `view`.

### Proof of Concept
```rust
use std::io;
use ciphersuite::Ciphersuite;
use frost::{curve::Secp256k1, ThresholdKeys};

// Build input: ID || t=n=65535 || i=1 || Lagrange || zero secret || n encoded points
let mut buf = vec![];
buf.extend((Secp256k1::ID.len() as u32).to_le_bytes());
buf.extend(Secp256k1::ID);
buf.extend(65535u16.to_le_bytes()); // t
buf.extend(65535u16.to_le_bytes()); // n
buf.extend(1u16.to_le_bytes());     // i
buf.push(1);                        // Interpolation::Lagrange
buf.extend(<Secp256k1 as Ciphersuite>::F::ONE.to_repr().as_ref()); // secret_share
for _ in 0 .. 65535 {
  buf.extend(Secp256k1::generator().to_bytes().as_ref()); // verification shares
}

// ~2 MB input -> ~4.3e9 field multiplications + 65535 inversions inside ThresholdKeys::new
let _ = ThresholdKeys::<Secp256k1>::read::<&[u8]>(&mut buf.as_ref());
```