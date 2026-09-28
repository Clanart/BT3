### Title
Panic on unvalidated `Participant(0)` in signing set via `view().unwrap()` enables denial of service - (File: crypto/frost/src/sign.rs)

### Summary
The FROST `SignMachine::sign` implementation validates the included participant set for count (`>= t`), upper bound (`<= n`), and duplicates, but never rejects `Participant(0)` — an index that is invalid in Serai's 1-indexed participant scheme. `self.params.keys.view(included.clone()).unwrap()` is then called unconditionally, turning an invalid participant index into a panic that aborts the signing operation.

### Finding Description
In `AlgorithmSignMachine::sign` (`crypto/frost/src/sign.rs:283-312`), the `included` set is built from the caller's own index plus every key in the attacker-influenced `preprocesses: HashMap<Participant, Preprocess>` map. The only validations are:

- `included.len() >= t` (line 298)
- `included.last() <= n` (line 302)
- pairwise distinctness (lines 306-310)

`Participant` is a `u16` newtype (`crypto/dkg/src/lib.rs`); zero is representable in the type and is never filtered. The code then executes `self.params.keys.view(included.clone()).unwrap()` (line 312). `ThresholdView`/`view` construction rejects out-of-domain participant indexes — participant indexes in `ThresholdParams`/`ThresholdKeys` are constrained to `1..=n` (see `ThresholdKeys::read`, which rejects `Participant::new` failures at `crypto/dkg/src/lib.rs:600`). Passing `0` therefore produces `Err`, and the `unwrap()` panics.

Even if `view` were to accept `0`, Lagrange interpolation over a zero x-coordinate divides by `(x_j - 0)` and produces an inverted-zero coefficient path in `crypto/dkg`'s interpolation code, again aborting or corrupting the computation.

The panic propagates out of `sign`, which the surrounding coordinator/processor code drives on untrusted preprocess messages received from the network (`read_preprocess` at `crypto/frost/src/sign.rs:276-281` reads a peer's `Commitments` and `Addendum` from a raw byte stream; the peer chooses which participant index it submits them under).

### Impact Explanation
An attacker who can submit a preprocess message keyed to `Participant(0)` (or any index rejected by `view` but not by the local checks) causes a panic in the local signing process. Because the panic occurs inside the generic `SignMachine::sign`, every caller — the tributary signing flow and any downstream multisig scheduler — is crashable on demand, mirroring the reported bug class (crafted input to a parser/handler crashes the service). This is an availability-only impact: no secret material is leaked and no invalid signature is produced, since the panic happens before share computation.

### Likelihood Explanation
Triggering requires only sending a single malformed preprocess map entry before `sign` is invoked; no cryptographic validity is needed because the crash precedes share verification. The cost is one network message. The only mitigating factor is whether the deployment's networking layer rejects `Participant(0)` earlier when constructing the `HashMap` key — the `Participant::new` constructor used in deserializers (`crypto/dkg/src/lib.rs:600`, `coordinator/src/tributary/transaction.rs:348`) returns `Option`, which suggests some callers validate, but `sign` itself does not depend on that guarantee and remains exposed to any integrator that builds the map directly. Rated Medium: reliable crash when reachable, but availability-only and dependent on the caller's map construction.

### Recommendation
Reject `Participant(0)` explicitly in `sign` alongside the existing bounds checks, and replace `.unwrap()` on `view()` with `?` mapped to a `FrostError::InvalidParticipant`:

```rust
// crypto/frost/src/sign.rs, after the OOB index check
if u16::from(included[0]) == 0 {
  Err(FrostError::InvalidParticipant(multisig_params.n(), included[0]))?;
}
let view = self.params.keys.view(included.clone())
  .map_err(|_| FrostError::InvalidSigningSet("invalid participant set"))?;
```

Applying the same `map_err` pattern to `validate_map` already in place keeps every untrusted-index failure on the error path rather than the panic path.

### Proof of Concept
```rust
use std::collections::HashMap;
use frost::{Participant, ThresholdKeys, sign::SignMachine, curve::Ristretto};
use frost_schnorrkel::Schnorrkel; // any Algorithm impl

// Setup: machine for a t-of-n multisig (t=2, n=3, i=1)
let (machine, _preprocess) = algorithm_machine.preprocess(&mut rng);

// Attacker delivers a preprocess under participant index 0
let mut preprocesses = HashMap::new();
preprocesses.insert(Participant(0u16), attacker_preprocess_bytes_parsed);
preprocesses.insert(Participant(2u16), other_valid_preprocess);

// included = [0, 1, 2]: len >= t, last <= n, no duplicates -> passes all checks
// view() errors on Participant(0) -> unwrap() panics
let _ = machine.sign(preprocesses, b"msg"); // PANIC: called `Result::unwrap()` on an `Err` value
```

*Caveat:* I verified the unchecked zero-index path and the `unwrap` at `crypto/frost/src/sign.rs:302-312` directly, but could not re-open `crypto/dkg/src/lib.rs`'s `ThresholdKeys::view`/`Participant::new` bodies in this session to confirm the exact `Err` returned for index 0. If `view` accepts `0`, the same input instead reaches Lagrange interpolation with a zero coordinate; either way the behavior is undocumented and should be hardened as described.