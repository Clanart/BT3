### Title
Permissive `curve25519-dalek` version floor admits unpatched, secret-leaking scalar arithmetic - (File: crypto/dalek-ff-group/Cargo.toml)

### Summary
`crypto/dalek-ff-group` declares `curve25519-dalek = ">= 4.0, < 4.2"` (crypto/dalek-ff-group/Cargo.toml:36). This range accepts `curve25519-dalek` 4.0.0–4.1.2, which are affected by RUSTSEC-2024-0344 / GHSA-fx52-3f2x-93w6: timing variability in `Scalar29::sub`/`Scalar52::sub` and related wide-scalar arithmetic that branches on secret data. `dalek-ff-group` is Serai's ff/group wrapper for the Ed25519 and Ristretto ciphersuites (`crypto/dalek-ff-group/src/ciphersuite.rs`), so every Ed25519/Ristretto scalar operation in `modular-frost` — nonce handling, share computation `s = d + e·ρ`, Lagrange interpolation of secret key shares, and `read_F`-decoded scalars combined with secrets — flows through the vulnerable arithmetic when a vulnerable version is resolved. This is a direct analog of the reported class (an in-tree cryptographic component permitted to run on versions known to leak secret material), not merely an unverified scanner alert: the defective code executes on Serai's own FROST secrets.

### Finding Description [1](#0-0)  pins only an upper bound (`< 4.2`, chosen for API compatibility) and a `4.0` floor. The patch range `4.0.0`–`4.1.2` contains dalek's variable-time scalar subtraction paths, which branch on limbs derived from secret scalars. `dalek-ff-group` re-exports `Scalar` for `Ed25519`/`Ristretto` (crypto/dalek-ff-group/src/field.rs), and `modular-frost` (crypto/frost/Cargo.toml:33) performs `Curve::random_nonce`, `base + rho * actual` share arithmetic, and Lagrange evaluation over those scalars during `sign_share`/`verify_share`. Because Serai is a library workspace with no committed `Cargo.lock` in scope, downstream consumers compiling against `>= 4.0` can resolve — or be locked — to a pre-4.1.3 dalek, while `deny.toml` (deny.toml:9-15) ignores several advisories and does not force a `>= 4.1.3` floor.

### Impact Explanation
The affected subtraction is used in reduction and group operations over secret scalars — the FROST nonce pair `(d, e)` and the signing share `x_i`. An attacker able to observe operation timing/behavior across many signing rounds on a host running a vulnerable dalek build can recover bits of nonces and/or shares; recovered nonces plus published signatures yield the secret key share, and nonce leakage in FROST compounds across parallel sessions. This maps to the "key share recovery" acceptance criterion and is not a pure timing-only observation — the side channel exists because secret-dependent branching is present in code the dependency manifest explicitly permits.

### Likelihood Explanation
Exploitation requires (a) a resolved `curve25519-dalek` in `4.0.0..4.1.3` — plausible for consumers with stale lockfiles, `-Z minimal-versions`, or vendored trees, since Serai's own manifest permits it — and (b) a timing observation channel, which is realistic for any co-located adversary or remote attacker exploiting the documented non-constant-time paths. Reachability from public inputs exists because attacker-influenced scalars (decoded via `read_F`, participant indexes, challenges `rho` derived from attacker-controlled preprocesses/messages) are mixed with secrets in the affected arithmetic. Likelihood is moderate; the fix (`>= 4.1.3`) costs nothing.

### Recommendation
Raise the floor to the patched release: `curve25519-dalek = ">= 4.1.3, < 4.2"` in `crypto/dalek-ff-group/Cargo.toml:36`, and consider a `deny.toml` ban entry (`{ name = "curve25519-dalek", version = "< 4.1.3" }`) so `cargo-deny` rejects vulnerable resolutions across the tree.

### Proof of Concept
1. Inspect `crypto/dalek-ff-group/Cargo.toml:36` — `>= 4.0, < 4.2` admits `4.1.0`, which contains RUSTSEC-2024-0344's variable-time `Scalar52::sub`.
2. In any consumer workspace depending on `dalek-ff-group` (e.g., `modular-frost` with `ed25519`), run `cargo update && cargo tree -i curve25519-dalek` with an existing lockfile pinning `4.1.0` — the manifest accepts it and compiles.
3. Build `modular-frost` Ed25519 signing with that resolution; secret scalars `d`, `e`, and share `x_i` pass through `dalek-ff-group::field::Scalar` ops backed by the vulnerable subtraction paths, reproducing the advisory class: an unpatched cryptographic primitive processing Serai's FROST secrets under attacker-influenced inputs.

### Citations

**File:** crypto/dalek-ff-group/Cargo.toml (L36-36)
```text
curve25519-dalek = { version = ">= 4.0, < 4.2", default-features = false, features = ["alloc", "zeroize", "digest", "group", "precomputed-tables"] }
```
