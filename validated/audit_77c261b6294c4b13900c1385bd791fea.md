### Title
`ThresholdKeys::read`/`ThresholdKeys::new` accept a zero secret share and identity/self-annihilating verification shares, yielding a group key with a publicly known (zero) private key — a threshold key with no effective owner (crypto/dkg/src/lib.rs)

### Summary
The external report describes `renounceOwnership()` leaving a contract owned by `address(0)` — i.e., control irrevocably handed to the null key. The Serai analog lives in `ThresholdKeys`: deserialization and construction never check that `secret_share != 0`, that the derived `group_key != identity`, or that the public-side analogue (`sum(verification_shares[i] * factor(i, 1..=t))`) is non-identity. A crafted `ThresholdKeys` serialization therefore decodes to a threshold key whose group private key is `0` — the cryptographic equivalent of transferring ownership to `address(0)`: the resulting group key is "ownerless" in the sense that no secret was ever committed to, yet anyone can sign for it.

### Finding Description
`ThresholdKeys::read` (crypto/dkg/src/lib.rs:574-632) reads `t`, `n`, `i`, an interpolation method, a `secret_share` via `C::read_F`, and `n` verification shares via `C::read_G`, then calls `ThresholdKeys::new`.

`ThresholdKeys::new` (crypto/dkg/src/lib.rs:349-391) validates only:
- the *count* of verification shares equals `n` (line 355),
- each participant index `<= n` (line 361-365),
- constant interpolation is only used when `t == n` (line 367-374).

It then computes the group key over only the first `t` participants (lines 376-378):

```rust
let t = (1 ..= params.t()).map(Participant).collect::<Vec<_>>();
let group_key =
  t.iter().map(|i| verification_shares[i] * interpolation.interpolation_factor(*i, &t)).sum();
```

No check is performed that:
- `secret_share != 0` (line 618 reads it unchecked; `DkgError::ZeroParameter` only covers `t`/`n`),
- any `verification_shares` entry is non-identity,
- `group_key != identity`.

With Lagrange interpolation over `t == n` participants, `group_key = sum_i share_i * λ_i` where `sum λ_i = 1`-style weights interpolate to `f(0) * G`. An attacker who supplies `verification_shares = {identity, ..., identity}` and `secret_share = 0` produces `group_key == identity` and `original_secret_share() == 0`. The decoded `ThresholdKeys` is self-consistent: `ThresholdView::secret_share()` interpolates to `0`, and the keys will happily sign — producing signatures valid under the public key `0·G` (the identity), whose discrete log is publicly known to be `0`.

Just as `renounceOwnership` leaves the vault controlled by `address(0)` — a key nobody owns — this produces a Serai multisig address controlled by private key `0` — a key *everybody* owns. Any funds the processor/scanner attributes to that group key (e.g., a Bitcoin multisig output derived via `networks/bitcoin/src/wallet`) are spendable by any party that knows elementary arithmetic, without any threshold collaboration.

A subtler variant keeps `group_key` non-identity but sets `secret_share = 0` while the verification shares encode a known constant: the deserialized holder contributes a known share, weakening the threshold to `t-1` honest shares plus the attacker's known zero — again a loss of ownership guarantees, undetected by construction.

### Impact Explanation
- A `ThresholdKeys` object decoded from untrusted bytes can represent a group key whose private key is `0` (identity group key) or otherwise attacker-known. Anyone can forge FROST/Schnorr signatures for it, and any outputs addressed to it are immediately stealable — funds reported received that are spendable by everyone rather than the validator set.
- Because `view()`/`secret_share()` return `0` consistently, signing sessions proceed normally and emit valid signatures, so the failure is silent: nothing in `ThresholdKeys::new`, `read`, or `view` detects the ownerless state.

### Likelihood Explanation
Reachability follows the enumerated surface: `ThresholdKeys::read` consumes a byte stream (`read_F`, `read_G`, params) with no provenance binding to a real DKG transcript — the encoded form (crypto/dkg/src/lib.rs:538-562) carries no commitments tying the shares to any participant's PoK, so any writer of the stream fully dictates `secret_share` and all `verification_shares`. Triggering requires only feeding such bytes; no threshold collusion, no validator status, and no online protocol participation is needed. The consistency checks that exist (count, index bounds, `t <= n`) are all satisfiable by the malicious encoding.

### Recommendation
In `ThresholdKeys::new` (crypto/dkg/src/lib.rs:349), reject:
- `secret_share.is_zero()`,
- any `verification_shares` value equal to `C::G::identity()`,
- a computed `group_key.is_identity()`.

Optionally also verify `group_key == verification_shares[i_local]·...`-style consistency between `secret_share` and `verification_shares[params.i()]` (i.e., `C::generator() * secret_share == verification_shares[&i]` under the interpolation) so a deserialized share provably belongs to the declared group key.

### Proof of Concept
```rust
use std::collections::HashMap;
use zeroize::Zeroizing;
use ciphersuite::Ciphersuite;
use dkg::{ThresholdParams, ThresholdKeys, Participant, Interpolation};

// For any Ciphersuite C (e.g. Ristretto, Secp256k1):
let t = 2u16; let n = 3u16;
let params =
  ThresholdParams::new(t, n, Participant::new(1).unwrap()).unwrap();

// All verification shares set to the identity point
let mut shares = HashMap::new();
for l in 1 ..= n {
  shares.insert(Participant::new(l).unwrap(), C::G::identity());
}

// Zero secret share: accepted without error
let keys = ThresholdKeys::new(
  params,
  Interpolation::Lagrange,
  Zeroizing::new(C::F::ZERO),
  shares,
)
.expect("zero secret share / identity shares are not rejected");

// group_key == identity => its discrete log is publicly known to be 0
assert!(bool::from(keys.group_key().is_identity()));

// view() also succeeds; the interpolated secret_share is 0, so the
// resulting signer emits signatures valid under private key 0, and any
// party can equivalently sign for this "multisig" alone.
let view = keys.view(vec![
  Participant::new(1).unwrap(),
  Participant::new(2).unwrap(),
]).unwrap();
assert!(bool::from(view.secret_share().is_zero()));
```

The same result is obtained purely through bytes: `ThresholdKeys::read` (crypto/dkg/src/lib.rs:574) happily decodes `[C::ID][t=2][n=3][i=1][Lagrange][F=0][G=id][G=id][G=id]`, since neither `read_F`/`read_G` results nor the derived `group_key` are checked for zero/identity anywhere in the constructor path.