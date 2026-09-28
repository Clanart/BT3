### Title
`ThresholdKeys::new` trusts that `verification_shares` contains participants `1..=n` and panics indexing a missing key when `Participant(0)` is admitted — (File: crypto/dkg/src/lib.rs)

### Summary
The kernel bug class is "a value tagged as trusted/non-NULL can actually be NULL, so NULL checks are elided and the dereference crashes." The Serai analog lives in `ThresholdKeys::new`: the key-validation loop assumes that a `verification_shares` map whose length equals `n` and whose keys are all `<= n` must contain every participant index `1..=n`. That invariant is false because `Participant(0)` is never rejected — the check `u16::from(participant) > params.n()` admits index `0`. A map `{0, 2, ..., n}` has length `n`, passes validation, and is missing index `1`, so the subsequent `verification_shares[i]` indexing for `i in 1..=t` panics on an absent `HashMap` key. [1](#0-0) 

### Finding Description
In `ThresholdKeys::new` (`crypto/dkg/src/lib.rs:349-391`):

- Shares-count check: `verification_shares.len() != n` → error.
- Per-participant check: only `participant > n` is rejected; `Participant(0)` passes because `0 > n` is false. There is no lower-bound check and no check that the set is exactly `1..=n`.
- `group_key` is then computed by indexing the map directly:

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

`HashMap::index` panics on a missing key, so any map containing `Participant(0)` (and therefore necessarily missing some index in `1..=t`, since `len == n`) causes an unconditional panic. The "trusted" assumption — `len == n && all keys <= n` ⟹ `keys == {1..=n}` — is the direct analog of the raw_tp "trusted ⟹ non-NULL" assumption; in both cases a single admissible out-of-domain value (`0` / `NULL`) violates the invariant the consumer relies on.

This is reachable through untrusted bytes: the code itself acknowledges "deserialize a semantically invalid FrostKeys" as a possible state (`crypto/frost/src/sign.rs:493`), and `ThresholdKeys::read`/`ThresholdCore` deserialization reconstructs the map and funnels into this constructor path. An attacker who can feed crafted serialized `ThresholdKeys` bytes to a `read`/`view`/`sign` path (or a malicious DKG contributor whose share map a coordinator normalizes into a `verification_shares` map) can trigger the panic. Additionally, even where `1..=t` are all present alongside `Participant(0)`, the extra `0` share is silently counted toward `n` while never being interpolated into `group_key`, meaning the map semantics are trusted beyond what validation establishes.

### Impact Explanation
An unprivileged party supplying crafted serialized threshold-key material (or a crafted share-commitment set that a host assembles into `verification_shares`) causes a panic in `ThresholdKeys::new`. In a Serai node/coordinator context this aborts signing-session setup — a denial of service reachable from public input bytes, matching the CVE's availability-only impact (CVSS 5.5, `A:H`). No secret leakage or forgery results, so severity is Medium.

### Likelihood Explanation
The panic requires only one out-of-range-low participant index (`0`) in the map — trivially constructible in a serialized `verification_shares`/`FrostKeys` blob. No discrete-log work, no race, no collusion threshold is needed; a single malformed input suffices wherever such deserialization is exposed to peer/integrator input.

### Recommendation
In `ThresholdKeys::new`, validate that the key set is exactly `{1 ..= n}`: reject `Participant(0)` explicitly (`participant == Participant(0)` or `u16::from(participant) == 0`), and use `verification_shares.get(i)` with a `DkgError` instead of panicking indexing. Apply the same `> 0` bound wherever participant indexes are accepted from untrusted input.

### Proof of Concept
```rust
// crypto/dkg ThresholdKeys::new panic trigger
// params: t = 2, n = 3
let mut verification_shares = HashMap::new();
// Insert Participant(0) plus participants 2 and 3 — len == n, all keys <= n
verification_shares.insert(Participant(0), point_a);
verification_shares.insert(Participant(2), point_b);
verification_shares.insert(Participant(3), point_c);

// Validation passes: len == 3 and no key > n
// group_key computation indexes verification_shares[Participant(1)] -> panic!
let _ = ThresholdKeys::<C>::new(params, Interpolation::Lagrange, share, verification_shares);
```

Note: I could not fully trace whether `ThresholdKeys::read` (or the coordinator's DKG finalization) routes deserialized maps through `ThresholdKeys::new` without an intervening normalization that filters `Participant(0)`; if a caller constructs the map strictly as `1..=n` internally, reachability is reduced to integrator-facing APIs. The validation gap itself is confirmed in the cited lines.

### Citations

**File:** crypto/dkg/src/lib.rs (L361-378)
```rust
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }

    match &interpolation {
      Interpolation::Constant(_) => {
        if params.t() != params.n() {
          Err(DkgError::InapplicableInterpolation("constant interpolation for keys where t != n"))?;
        }
      }
      Interpolation::Lagrange => {}
    }

    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```
