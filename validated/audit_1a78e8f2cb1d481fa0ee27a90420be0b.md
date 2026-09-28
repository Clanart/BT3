### Title
Attacker-controlled `ThresholdKeys` serialization yields arbitrary interpolation coefficients and an attacker-known group key - (File: crypto/dkg/src/lib.rs)

### Summary
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574) accepts a byte-discriminant selecting `Interpolation::Constant(Vec<F>)`, deserializing `n` caller-chosen scalar coefficients, plus an arbitrary `secret_share` and `verification_shares`. `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) performs no consistency checks: it never verifies `C::generator() * secret_share == verification_shares[i]`, never verifies the shares lie on a common polynomial, and for `Constant` interpolation only checks `t == n`. The interpolation factors used for signing are therefore fully attacker-controlled scalars `c[i-1]` (`interpolation_factor`, line 228). An attacker who supplies a crafted blob to a node loading keys (the rules expressly make `ThresholdKeys::read` a reachable untrusted-bytes entrypoint) can cause the node to hold and sign under a group key whose discrete logarithm the attacker knows.

### Finding Description
Two missing validations compose into the bug:

1. **Attacker-chosen interpolation coefficients.** `ThresholdKeys::read` reads a single byte: `0` → `Interpolation::Constant`, reading `n` scalars via `C::read_F` (lines 604-616). In `view()` / `interpolation_factor` (line 226-249), `Constant(c)` returns `c[usize::from(u16::from(i) - 1)]` — i.e., the per-participant Lagrange-equivalent weight is whatever scalar the serialized blob claimed. `ThresholdKeys::new` only rejects `Constant` when `t != n` (lines 367-373), and computes `group_key` as `Σ verification_shares[i] * c[i-1]` over `i = 1..=t` (lines 376-378).

2. **No share/verification-share consistency check.** `ThresholdKeys::new` stores `secret_share` without ever checking it corresponds to `verification_shares[params.i()]` or that the shares interpolate to the computed `group_key`. Every legitimate constructor (dealer `key_gen`, PedPoP `calculate_share`, `musig`) produces consistent keys, but `read` bypasses all of them.

Concretely, the attacker picks a known scalar `s`, sets every `verification_shares[i] = C::generator() * s`, every `secret_share = s`, and coefficients `c = [1, 0, 0, …]` (or any weights summing to 1 under the Constant convention). `group_key` computes to `G*s` — a key fully controlled by the attacker — and each participant's `view()` produces `secret_share = c[i-1] * s` with matching interpolated verification shares (crypto/dkg/src/lib.rs:494-521). The resulting signature shares are internally consistent and the aggregate FROST signature verifies under the attacker-chosen key.

This mirrors CVE-2019-19604: structured attacker-supplied data (the `.gitmodules` file there; the serialized `ThresholdKeys` blob here) is trusted to drive a privileged operation (command execution there; selection of the node's signing polynomial coefficients and group key here) without validation.

### Impact Explanation
A node that loads attacker-supplied `ThresholdKeys` (key restoration, migration, or any pipeline where key material bytes cross a trust boundary — the API surface explicitly treats `ThresholdKeys::read` as handling untrusted bytes) will emit valid FROST signature shares under a group key whose secret the attacker knows outright. The attacker can both sign unilaterally (they chose `s`) and harvest the node's protocol-valid shares. Any funds or authorizations gated on that group key are fully attacker-controlled. For Constant interpolation the attacker can additionally set arbitrary weights, e.g. concentrate the entire key on `included[0]` or nullify other participants' contributions (`c_i = 0` produces an identity verification share and a zero secret share contribution while still passing `view()`).

### Likelihood Explanation
Exploitation requires the node to deserialize keys from attacker-influenced bytes, which is exactly the scenario `ThresholdKeys::read` exposes and the rules scope in. No protocol round, collusion, or privileged access is needed — a single crafted blob suffices, and all checks in `read`/`new` (ciphersuite ID, `t <= n`, `i <= n`, `verification_shares.len() == n`, participant indexes `<= n`) pass.

### Recommendation
In `ThresholdKeys::new`, verify `C::generator() * *secret_share == verification_shares[&params.i()]`; for `Interpolation::Constant`, additionally validate the coefficients (e.g., require they sum such that reconstruction is non-degenerate, or restrict `Constant` interpolation to the `musig` constructor by making it unconstructable via `read`). At minimum, `read` should reject `Constant` interpolation blobs or perform a full `t`-of-`n` consistency check interpolating the verification shares at index 0 against `group_key`.

### Proof of Concept
```rust
// Attacker: target any Ciphersuite C. s is a scalar the attacker knows.
let s = C::F::random(&mut OsRng);               // attacker-known secret
let n = 3u16; let t = 3u16;                     // t == n required for Constant

// Serialize a ThresholdKeys blob (little-endian, per write()):
//   id_len || C::ID || t || n || i || 0x00 || c_1..c_n || s || V_1..V_n
// with c = [1, 0, 0], V_j = G*s for all j, secret_share = s.

// Node:
let keys = ThresholdKeys::<C>::read(&mut blob.as_slice()).unwrap();
// No error: all length/index checks pass; group_key() == C::generator() * s.
assert_eq!(keys.group_key(), C::generator() * s);   // attacker-controlled key

// Node i produces a ThresholdView; participant 1's share is s, others' are 0.
let view = keys.view(vec![p1, p2, p3]).unwrap();
// view.secret_share() == c[i-1] * s  → fully known to the attacker.
// FROST sign() yields shares aggregating to a valid signature for G*s,
// a key the attacker can also sign for alone.
```

Every claim above is backed by crypto/dkg/src/lib.rs: `read` (lines 574-632), `Interpolation::Constant` indexing (line 228), missing consistency checks and `group_key` derivation in `new` (lines 349-391), and the `view()` interpolation (lines 494-521).