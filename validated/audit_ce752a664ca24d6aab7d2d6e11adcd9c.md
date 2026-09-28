### Title
`Debug` on `ThresholdKeys` / `ThresholdView` leaks every participant's secret key share via `Interpolation::Constant` - (File: crypto/dkg/src/lib.rs)

### Summary
`Interpolation` derives `Debug`, and its `Constant(Vec<F>)` variant holds "a list of constant coefficients, one for each of the secret key shares" — i.e., the raw secret shares themselves (`crypto/dkg/src/lib.rs:211-222`). The manual `Debug` impls for `ThresholdCore` (line 266-276) and `ThresholdView` (line 316-329) both serialize the `interpolation` field, and `ThresholdKeys` derives `Debug` (line 291), which delegates to `ThresholdCore`. Any `{:?}`/`{keys:?}` debug output, `format!("{e:?}")` error path, or log line that formats these structures writes every participant's secret key share in plaintext to the log.

### Finding Description
The codebase deliberately hand-writes `Debug` impls to omit secret material: `ThresholdCore` omits `secret_share`, `SecretShare` prints only `finish_non_exhaustive()`, and `Encryption`/`KeyMachine`/`SecretShareMachine` omit their keys (`crypto/dkg/pedpop/src/lib.rs:242-245`, `crypto/dkg/pedpop/src/encryption.rs:410-419`). But `Interpolation::Constant` was overlooked: it is derived-`Debug` public data by construction, yet `ThresholdKeys::new` accepts it whenever `t == n` (`crypto/dkg/src/lib.rs:367-374`), and `interpolation_factor` confirms `c[i-1]` is used exactly as participant `i`'s key share (line 228). Because `ThresholdCore` is shared via `Arc` inside `ThresholdKeys`, a single debug print of a `ThresholdKeys` (or `ThresholdView`, which copies `interpolation` into itself at `crypto/dkg/src/lib.rs:306` and prints it at line 320) discloses all `n` constant coefficients — the complete set of secret shares — allowing reconstruction of the group private key by anyone who can read the log.

### Impact Explanation
Log disclosure of all secret key shares = full compromise of the threshold group key. An attacker with read access to logs (a much lower bar than key access — exactly the CWE-532 scenario in the reference advisory) recovers the group's private key and can forge arbitrary FROST/Schnorr signatures and steal all funds controlled by the threshold wallet.

### Likelihood Explanation
`ThresholdKeys`/`ThresholdView` are the primary exported key types of `crypto/dkg` and are passed around signers, the processor key DB, and network machines, all of which derive or propagate `Debug`. It only takes one `log::debug!("{keys:?}")`, an `unwrap`/panic message, or an error-formatted with `{:?}` during development or incident debugging for shares to hit persistent logs. `Interpolation::Constant` is a supported configuration (`t == n` multisig deployments), so this is not a dead code path.

### Recommendation
Implement `Debug` for `Interpolation` manually (e.g., print the variant name via `finish_non_exhaustive()` without the coefficient vector), matching the pattern already used for `SecretShare`, `ThresholdCore`, `Encryption`, and `KeyMachine`. Alternatively, store the constant coefficients in `Zeroizing<Vec<F>>` and omit them from `Debug`.

### Proof of Concept
```rust
// crypto/dkg: t == n configuration uses Constant interpolation whose Vec<F>
// IS the vector of secret key shares.
let shares: Vec<Zeroizing<F>> = /* shares for participants 1..=n */;
let interpolation = Interpolation::Constant(shares.iter().map(|s| **s).collect());
let keys = ThresholdKeys::<C>::new(params, interpolation, my_share, vshares)?;

// Any of these lines writes every participant's secret share to output:
log::debug!("keys: {keys:?}");        // ThresholdKeys -> ThresholdCore -> interpolation
// or:
let view = keys.view(&included)?;
log::debug!("view: {view:?}");        // prints `interpolation` field again
// Format of Interpolation::Constant is the derived Debug: `Constant([F, F, ...])`,
// each element a full scalar == that participant's secret share.
```
Root cause: `#[derive(Debug)]` on `Interpolation` at `crypto/dkg/src/lib.rs:211` combined with `.field("interpolation", &self.interpolation)` at lines 273 and 320, while `c[i-1]` at line 228 proves the logged values are the secret shares themselves.