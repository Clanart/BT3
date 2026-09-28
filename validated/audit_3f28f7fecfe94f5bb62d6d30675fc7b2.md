### Title
`ThresholdKeys::new` derives `group_key` from only participants `1..=t` and never checks share/polynomial consistency — a malformed key set yields signatures that do not verify under the reported group key - (File: crypto/dkg/src/lib.rs)

### Summary
The Nowswap bug class is a missing/incorrectly-scoped invariant check (`x·y = k` verified against stale/insufficient state) that let each swap extract more value than it was owed. The Serai analog is `ThresholdKeys::new` (and the corresponding `ThresholdKeys::read` deserialization path), which computes the reported `group_key` by Lagrange-interpolating **only** the verification shares of participants `1..=t`, while never verifying (a) that the local `secret_share` matches `verification_shares[params.i()]`, nor (b) that the verification shares of participants `t+1..=n` lie on the same degree-`(t-1)` polynomial. The "invariant" — *all* verification shares interpolate to the same group key — is checked on only a `t`-sized subset, exactly the shape of Nowswap checking `k` on one pair state while the real reserves had changed. [1](#0-0) [2](#0-1) 

### Finding Description
`ThresholdKeys::new` performs only two structural checks on `verification_shares`: the map length equals `n`, and no index exceeds `n` (`crypto/dkg/src/lib.rs:355-365`). It then derives `group_key` as `Σ verification_shares[i] * λ_i` over `t = participants 1..=t` only (`crypto/dkg/src/lib.rs:376-378`). Nothing binds:

- `secret_share` to `verification_shares[params.i()]` (i.e., `C::generator() * secret_share == verification_shares[i]` is never asserted), and
- `verification_shares[t+1..=n]` to the polynomial defined by `1..=t`.

Later, `ThresholdView::view` interpolates each signing set's verification shares per the stored map and adds the offset to `included[0]` (`crypto/dkg/src/lib.rs:500-521`), and `Schnorr::verify` checks the aggregate `s·G == R + c·group_key` using the stored `group_key` (`crypto/frost/src/algorithm.rs:214-217`). The aggregate only verifies if `Σ λ_i·Y_i == group_key` for the *actual* signing set — which holds iff every `Y_i` lies on the polynomial fixed by shares `1..=t`. For any key set where a share `Y_j` (`j > t`) is off-polynomial, every signing set containing `j` produces individually valid shares (per-share blame checks pass, `crypto/frost/src/sign.rs:474-489`) yet the combined signature fails verification under the published `group_key` — or, if the inconsistency is arranged across shares, fails as `FrostError::InternalError("everyone had a valid share yet the signature was still invalid")`.

The reachable path from public inputs is `ThresholdKeys::read` on untrusted bytes (the in-scope surface explicitly includes `ThresholdKeys::read`): a hostile bytes supplier, or any integrator path that reconstructs `ThresholdKeys`/`FrostKeys` from transmitted data rather than from `BlameMachine::complete`, can install a key set whose reported `group_key`/`original_group_key` does not correspond to any secret the stored shares can sign for.

### Impact Explanation
Funds/operations keyed to `group_key()` are unspendable: the network observes a group key (e.g., a Bitcoin/Taproot output key derived from `group_key`), receives funds to it, yet no signing set containing an off-polynomial participant can ever produce a signature that verifies — matching the accepted "funds reported received that are not spendable" impact. Additionally, the mismatch between per-share validity and aggregate failure defeats blame attribution: `complete` first accepts the aggregate only if it verifies (`crypto/frost/src/sign.rs:462-467`), and the blame path confirms each share individually, terminating in `InternalError` — a consensus-splitting, unblameable abort. Severity: High (loss of funds availability), conservatively Medium if the malformed-keys path is only reachable via integrator misuse rather than wire bytes.

### Likelihood Explanation
Honest PedPoP-DKG outputs are always consistent (`calculate_share` derives `verification_shares` and the secret from verified commitments, `crypto/dkg/pedpop/src/lib.rs:510-531`), so the bug only manifests on the deserialization/reconstruction path. The prompt's in-scope surface explicitly includes `ThresholdKeys::read` fed with untrusted bytes; any coordinator/peer that can cause a node to load a crafted key set — or any serialization round-trip that reorders/corrupts the `verification_shares` map — hits it with deterministic probability. No threshold collusion is required; a single malformed share suffices.

### Recommendation
In `ThresholdKeys::new` (and symmetrically in the `read` path, which should route through `new`):

```rust
// 1. Bind the local secret share to its verification share
if C::generator() * secret_share.deref() != verification_shares[&params.i()] {
  Err(DkgError::InvalidVerificationShare)?;
}
// 2. Verify every share lies on the degree-(t-1) polynomial fixed by 1..=t
for j in (t+1)..=n {
  let expected: C::G = (1..=t)
    .map(|i| verification_shares[&Participant(i)] * interpolation_factor over set {1..=t} evaluated at j)
    .sum();
  if expected != verification_shares[&Participant(j)] {
    Err(DkgError::InvalidVerificationShare)?;
  }
}
```

Equivalently, check that interpolating `verification_shares` over *any* `t`-subset containing `j > t` reproduces `group_key`, or store the commitments polynomial and validate each share against it.

### Proof of Concept
```rust
// Construct a syntactically valid, semantically inconsistent key set for a
// 2-of-3 (t=2, n=3) group over any in-scope Ciphersuite.

let params = ThresholdParams::new(2, 3, Participant::new(1).unwrap()).unwrap();

let mut rng = OsRng;
let x1 = C::random_nonzero_F(&mut rng);
let x2 = C::random_nonzero_F(&mut rng);
let x3_bad = C::random_nonzero_F(&mut rng); // NOT on the polynomial through (1,x1),(2,x2)

let mut verification_shares = HashMap::new();
verification_shares.insert(Participant::new(1).unwrap(), C::generator() * x1);
verification_shares.insert(Participant::new(2).unwrap(), C::generator() * x2);
// Off-polynomial share for participant 3:
verification_shares.insert(Participant::new(3).unwrap(), C::generator() * x3_bad);

let keys = ThresholdKeys::new(
  params,
  Interpolation::Lagrange,
  Zeroizing::new(x1),
  verification_shares,
).unwrap(); // Accepted: only count/bounds are checked (lib.rs:355-365)

// group_key = interpolate over participants {1,2} only (lib.rs:376-378)
let group_key = keys.group_key();

// Any signing set including participant 3 produces shares that pass the
// per-share batch check (sign.rs:474-489, algorithm.rs:219-230) yet whose
// sum fails `s·G == R + c·group_key` (algorithm.rs:214-217), ending in
// FrostError::InternalError — an unblameable failure for a key the network
// may already hold funds under. The same state is reachable by serializing
// a corrupted verification_shares map and re-reading via ThresholdKeys::read.
```

Root cause confirmed: `crypto/dkg/src/lib.rs:376-378` interpolates `group_key` from shares `1..=t` alone while `crypto/dkg/src/lib.rs:355-365` checks only count/bounds, and nothing ties `secret_share` or shares `t+1..=n` to that polynomial.

### Citations

**File:** crypto/dkg/src/lib.rs (L355-365)
```rust
    if verification_shares.len() != usize::from(params.n()) {
      Err(DkgError::IncorrectAmountOfVerificationShares {
        n: params.n(),
        shares: verification_shares.len(),
      })?;
    }
    for participant in verification_shares.keys().copied() {
      if u16::from(participant) > params.n() {
        Err(DkgError::InvalidParticipant { n: params.n(), participant })?;
      }
    }
```

**File:** crypto/dkg/src/lib.rs (L376-378)
```rust
    let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
    let group_key =
      t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```
