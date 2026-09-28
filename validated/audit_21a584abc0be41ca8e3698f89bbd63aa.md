### Title
Secret key material leaked via `Debug` formatting of `ThresholdKeys` / `ThresholdView` - ([File: crypto/dkg/src/lib.rs])

### Summary
Like CVE-2018-1264 (secret material written to logs via a diagnostic dump), Serai's DKG leaks secret scalars through `fmt::Debug`. `Interpolation::Constant(Vec<F>)` holds the private per-participant coefficients ("one for each of the secret key shares") and derives `Debug`. `ThresholdCore`'s and `ThresholdView`'s manual `Debug` impls print the `interpolation` field, and `ThresholdKeys` derives `Debug` — so formatting any of these types with `{:?}` emits the secret coefficients (and for `ThresholdKeys`, the `scalar`/`offset` tweaks) into whatever log/diagnostic sink consumes the output. Anyone able to read those logs (or who receives an error/Debug dump over the wire) recovers participants' secret shares directly, bypassing the threshold entirely.

### Finding Description
- `Interpolation<F>` derives `Debug` at `crypto/dkg/src/lib.rs:211`, and its `Constant` variant is documented as "A list of constant coefficients, one for each of the secret key shares" (`crypto/dkg/src/lib.rs:214-219`). For `t == n` key sets — the only configuration where `Constant` is permitted (`crypto/dkg/src/lib.rs:367-373`) — these coefficients are the private key shares themselves.
- `ThresholdCore`'s manual `Debug` impl deliberately omits `secret_share` but still prints `interpolation` (`crypto/dkg/src/lib.rs:266-276`), so the `Constant` coefficients are emitted.
- `ThresholdView`'s `Debug` impl likewise prints `interpolation` (`crypto/dkg/src/lib.rs:316-328`) while omitting `secret_share`.
- `ThresholdKeys` derives `Debug` (`crypto/dkg/src/lib.rs:291`), which formats `core` (delegating to `ThresholdCore`'s `Debug`, leaking the coefficients) plus `scalar` and `offset` — ephemeral tweaks that can themselves be secret (e.g. private offset scalars applied via `offset()` / `scale()`, `crypto/dkg/src/lib.rs:400-417`).
- The inconsistency shows intent: both manual impls use `finish_non_exhaustive` specifically to hide `secret_share`, yet the `Constant` coefficients are functionally equivalent secret material and are printed.

### Impact Explanation
`Debug`-formatting a `ThresholdKeys`/`ThresholdView`/`ThresholdCore` — in a panic message, `log::debug!`, error context, `unwrap_err` path, or RPC diagnostic — writes the `Constant` interpolation coefficients (all participants' private shares for `t == n` multisigs, usable to reconstruct the group secret key outright) and the active `scalar`/`offset` tweaks into logs. An attacker who can read the node's logs, crash dumps, or error reports — precisely the access model in CVE-2018-1264 — obtains the key shares without defeating any cryptography. For `Lagrange` interpolation the coefficient leak doesn't apply, but `scalar`/`offset` still leak.

### Likelihood Explanation
`ThresholdKeys` is the type persisted and passed throughout the signing stack (`crypto/frost`, `processor`), and `#[derive(Debug)]` exists precisely so it gets formatted in traces and error paths. `Constant` interpolation is used by the recovery/promote flows in `crypto/dkg`. No malicious participant is required — only local log read access, matching the original CVE's threat model.

### Recommendation
- Remove `Debug` from `Interpolation`'s derive, or implement it manually to elide `Constant` coefficient contents (e.g. print only the variant tag and length).
- Implement `Debug` manually for `ThresholdKeys` (mirroring `ThresholdCore`'s approach) to omit `scalar`/`offset`, or only print non-secret fields (`params`, `group_key`, `verification_shares` count).
- Audit other secret-bearing types (`EncryptedMessage`, `EncryptionKeyProof` — which derives `Debug` over the ECDH `key` at `crypto/dkg/pedpop/src/encryption.rs:260-264`, nonce generators) for the same leak class.

### Proof of Concept
Conceptual PoC against `crypto/dkg`:

```rust
// t == n, Constant interpolation — coefficients are the private shares
let params = ThresholdParams::new(n, n, Participant::new(i).unwrap()).unwrap();
let keys = ThresholdKeys::<C>::new(
    params,
    Interpolation::Constant(secret_coefficients.clone()), // each is a secret share
    secret_share,
    verification_shares,
).unwrap();

// Any logging / panic / error formatting of the keys
let leaked = format!("{keys:?}");
// `leaked` contains every coefficient of `secret_coefficients`
// and keys.scalar / keys.offset, recoverable by anyone reading the log line.
```

Similarly `format!("{:?}", keys.view(included).unwrap())` prints the `Constant` vector via `ThresholdView`'s `Debug` impl at `crypto/dkg/src/lib.rs:316-328`. The leaked coefficients for a `t == n` set sum directly to the group's secret key, yielding full key recovery from a single log line.