### Title
Attacker-controlled `Participant` index escapes the valid signer domain (index `0` never rejected), letting a malicious preprocess resolve outside the intended participant set — (`File: crypto/frost/src/sign.rs`)

### Summary
The AIL path-traversal bug class — a user-supplied identifier joined to a fixed base so the result resolves outside the intended domain — maps onto Serai's signing path as an *identifier-traversal*: the participant indexes in the `preprocesses` map supplied to `SignMachine::sign` are attacker-controlled identifiers, and the validation in `AlgorithmSignMachine::sign` only bounds them above (`> n`) and checks duplicates, but never rejects `Participant(0)`. Index `0` is outside the DKG's valid participant domain (`1..=n`), yet it is admitted into `included`, sorted into position 0, fed to `ThresholdKeys::view`, counted toward the threshold, transcribed into the rho binding transcript, and passed to `verify_share` / `bound` during blame.

### Finding Description
In `AlgorithmSignMachine::sign` (`crypto/frost/src/sign.rs:283-312`), the signer builds the signing set from keys of the caller-supplied `HashMap<Participant, Preprocess>`:

```rust
// crypto/frost/src/sign.rs
included.push(multisig_params.i());
for l in preprocesses.keys() {
  included.push(*l);
}
included.sort_unstable();

// Included < threshold
if included.len() < usize::from(multisig_params.t()) { ... }
// OOB index
if u16::from(included[included.len() - 1]) > multisig_params.n() { ... }
// Same signer included multiple times
for i in 0 .. (included.len() - 1) {
  if included[i] == included[i + 1] { ... }
}
```

Only the *maximum* is checked against `n`; there is no lower bound, so `Participant(0)` passes all three checks. The forged identifier then flows to:

- `self.params.keys.view(included.clone())` (`sign.rs:312`) — the participant set enters Lagrange interpolation. Interpolating at `x = 0` is the constant term of the polynomial: the verification share resolved for index `0` is the **group public key itself**, and the corresponding secret-share slot is the group private key. A party that was never issued share 0 is thus "resolved" to a position the DKG never allocated — the exact analog of a resolved path escaping the storage directory.
- The threshold check counts the phantom participant, so `included.len() >= t` can be satisfied with fewer real key-holders than `t`.
- The rho transcript appends `participant = 0` and its attacker-chosen `Commitments`/addendum (`sign.rs:325-358`), binding the forgery into every honest signer's challenge.
- In `complete`, `self.view.included()` iterates index `0`; `verify_share(self.view.verification_share(0), self.B.bound(0), responses[0])` (`sign.rs:475-484`) verifies the attacker's submitted share against the group key, and `verify_vartime_with_vartime_blame` returns `Participant(0)` as the blamed party — an index that exists in no participant registry.

### Impact Explanation
Two concrete consequences:

1. **Domain-confusion in blame/verification**: a malformed session resolves a "verification share" for a nonexistent participant equal to the group key. Any blame machinery or downstream coordinator logic that trusts `view.included()` now operates on an identifier outside the issued-share domain — shares can be attributed to `Participant(0)`, which no key was ever generated for.
2. **Signing-set manipulation / unintended signing context**: the phantom index inflates `included` toward `t` and shifts `included[0]`. Where a `ThresholdView` offset is applied relative to the first included participant (the documented offset-on-`included[0]` behavior used by algorithms such as the Bitcoin tweaked-Schnorr algorithm), an adversary controlling the lowest index claims the offset-bearing position, altering which share carries the algorithm offset. In combination with the attacker-chosen commitments bound into `rho`, this lets a below-threshold adversary steer sessions into signing-set configurations the honest participants never agreed to, and reliably attribute failure to a nonexistent party.

The impact is bounded: it does not by itself yield the group secret (a share keyed at index 0 must still verify against the group key, i.e. requires the full private key to produce a *valid* share), so the demonstrated impact is unauthorized set manipulation plus blame misattribution — consistent with Medium severity, matching the source advisory.

### Likelihood Explanation
Reachability is direct for any party that can submit a FROST preprocess message to a signer: `Preprocess` is read via `read_preprocess` (`sign.rs:276-281`) and the caller places it in the map under a `Participant` key of the caller's choosing. No privileged position is needed — a single malicious or impersonated preprocess origin suffices, and honest signers have no code path that rejects index 0. The constraint is that a malformed session will fail at `complete` unless the attacker also satisfies verification for index 0, so exploitation yields session corruption and blame forgery rather than a valid forged signature.

### Recommendation
In `AlgorithmSignMachine::sign` (`crypto/frost/src/sign.rs`, ~line 301), validate the *minimum* of `included` as well as the maximum: reject `included[0] == Participant(0)` (equivalently, enforce `1 <= u16::from(l) <= n` for every key in `preprocesses`, mirroring `validate_map`). Apply the same check anywhere a `HashMap<Participant, _>` keyed by remote parties is turned into a signing set or view (`complete`'s `shares` map is protected indirectly via `validate_map` against `view.included()`, but `view` itself should reject zero so callers can't construct it).

### Proof of Concept
Conceptual, against `crypto/frost`:

```rust
// Attacker is a legitimate participant i; it additionally injects a
// preprocess under the never-issued index 0.
let mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>> = received();
preprocesses.insert(Participant::new(0).unwrap(), forged_preprocess);

// Honest signer with params.i() = some nonzero index:
let (sig_machine, share) = sign_machine.sign(preprocesses, msg).unwrap();
// - `included` becomes [0, i, ...]; all three checks pass.
// - `included.len()` counts the phantom party toward `t`.
// - `keys.view(included)` interpolates at x=0; verification_share(0) == group_key.
// - In `complete`, blame is reported as InvalidShare(Participant(0)).
```

The root cause is verified concretely in `crypto/frost/src/sign.rs:290-312`: the only bound enforced on remote-supplied `Participant` keys is `<= n`, leaving index `0` — outside every issued-share domain — admitted into the signing set.