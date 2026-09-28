### Title
Missing lower-bound check on participant index enables use of an out-of-range (zero) participant index in the signing set, causing a panic / invalid `ThresholdView` during FROST signing - ([File: crypto/frost/src/sign.rs](crypto/frost/src/sign.rs))

### Summary
The vim bug class is use-after-free: state is accessed after/without valid allocation bounds, crashing the application. In Serai's memory-safe Rust the analogous shape is an index validated only on one side: `AlgorithmSignMachine::sign` builds `included` from attacker-supplied `preprocesses` keys and checks `included.last() > n`, but never checks `included[0] >= 1`. Participant indexes in this system are defined as `1..=n` (FrostError's own message reads `"invalid participant (0 < participant <= {0}...)"` at crypto/frost/src/lib.rs:33), yet a `Participant(0)` entry is accepted, producing a `ThresholdView` over an index that owns no verification share — dereferencing state that was never allocated for it.

### Finding Description
In `crypto/frost/src/sign.rs` `AlgorithmSignMachine::sign` (lines 283–313):

```rust
let mut included = Vec::with_capacity(preprocesses.len() + 1);
included.push(multisig_params.i());
for l in preprocesses.keys() {
  included.push(*l);
}
included.sort_unstable();

if included.len() < usize::from(multisig_params.t()) { ... }
// OOB index — only the upper bound is checked
if u16::from(included[included.len() - 1]) > multisig_params.n() { ... }
for i in 0 .. (included.len() - 1) {
  if included[i] == included[i + 1] { Err(DuplicatedParticipant) }
}

let view = self.params.keys.view(included.clone()).unwrap();
validate_map(&preprocesses, &included, multisig_params.i())?;
```

`preprocesses` is a `HashMap<Participant, Preprocess>` populated entirely from peer-supplied bytes via `read_preprocess` (coordinator/processor feed serialized preprocesses received over the network into `machine.sign(preprocesses, msg)`). A peer can insert key `Participant(0)`. The duplicate check and the `> n` check both pass for `0`. `validate_map` only checks membership, so it also passes. `self.params.keys.view(included)` then computes a `ThresholdView` containing index `0`, for which no verification share exists in `ThresholdKeys::new` (shares are generated for `1..=n`).

Two consequences follow:

1. On `complete`, `self.view.verification_share(Participant(0))` is dereferenced during per-share blame verification (crypto/frost/src/sign.rs:475–485), indexing state that does not exist → panic (out-of-bounds) or, depending on the `ThresholdView` representation, silently aliasing the group key / index-0 slot — the "access of freed/never-valid window structure" analog.
2. Because `included[0]` is the lowest ID, Serai's additive-offset scheme explicitly adds the transcripted scalar offset to `included[0]` (per `spec/cryptography/FROST.md`: "the offset is added to the participant with the lowest ID"). With `included[0] == 0`, the offset is credited to the attacker's fabricated index-0 entry rather than to a real signer, corrupting the offset accounting in the view.

### Impact Explanation
Any peer able to submit a preprocess for a signing session (a normal, unprivileged threshold participant — the same reachability as every other preprocess-based issue) can trigger a panic in every other signer's `sign`/`complete` path for that session, or obtain a `ThresholdView` whose lowest-index member is a phantom participant holding the additive offset. At minimum this is a remotely reachable crash/abort of the signing attempt (matching the CVE's availability impact); in the offset case it produces a verifier formula that attributes the key offset to a non-existent party — an incorrect verifier formula per the acceptance criteria.

### Likelihood Explanation
Reachable whenever a signing session accepts preprocesses from peers: `read_preprocess` → `sign(preprocesses, msg)` is the documented external path, and `Participant` is just a `u16` deserialized from the message map key — nothing on that path rejects `0`. Requires only a single malicious preprocess entry; no collusion, no BFT violation, no unsafe code.

### Recommendation
In `AlgorithmSignMachine::sign`, extend the bounds check to the lower end: reject `included[0] == Participant(0)` (e.g., `if u16::from(included[0]) == 0 { Err(FrostError::InvalidParticipant(multisig_params.n(), included[0])) }`). Equivalently, validate every included participant satisfies `1 <= i <= n`. The same check should be applied anywhere else `included`/`Participant` values from untrusted input feed `ThresholdKeys::view` or `verification_share`.

### Proof of Concept
Within the library (attacker is participant `i_a` in an `n`-of-`t` FROST session):

```rust
// Attacker builds the preprocesses map delivered to a victim signer:
let mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>> = HashMap::new();
// ... other honest participants' preprocesses ...
// Fabricated preprocess under the never-allocated index 0:
preprocesses.insert(Participant::from(0u16), attacker_crafted_preprocess);
// Victim calls:
machine.sign(preprocesses, msg);
```

`included` becomes `[0, ...]`; all validation passes; `keys.view(included)` constructs a view for index 0. On `complete`, `view.verification_share(Participant(0))` panics (or resolves to group-key state) and the offset, if the algorithm defines one, is added to index 0's share instead of the true lowest signer.

Caveat: the exact internal behavior of `ThresholdKeys::view`/`verification_share` at index 0 (panic vs. aliasing the group-key slot) could not be fully confirmed in the available iterations since `crypto/dkg/src/lib.rs` internals weren't read; either outcome is a defect — panic (DoS) or an incorrect verifier formula.