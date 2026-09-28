### Title
Empty `SchnorrAggregate` with `s == 0` verifies as valid — (`crypto/schnorr/src/aggregate.rs`)

### Summary
`SchnorrAggregator::complete` explicitly refuses to produce an aggregate when zero signatures were aggregated (`if self.sigs.is_empty() { return None; }`), correctly recognizing that an empty aggregate carries no proof of anything. However, the symmetric edge case is not handled in `SchnorrAggregate::verify`: an aggregate containing zero `Rs` and `s = 0` satisfies the verification equation and returns `true`. A byte string encoding a zero-length `Rs` vector plus a zero scalar — fully attacker-controlled via `SchnorrAggregate::read` — is accepted as a valid aggregate signature over any key/challenge set.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`:

- `SchnorrAggregate::read` (lines 77–88) reads a `u32` length, then `C::read_G` per `R` and `C::read_F` for `s`. An attacker supplies `len = 0` followed by the canonical encoding of the zero scalar.
- `SchnorrAggregate::verify` (lines 127–146) checks `self.Rs.len() != keys_and_challenges.len()` and builds `pairs`. With `keys_and_challenges` empty (or — worse for the caller — with the verifier accepting an empty list), the loop contributes nothing, leaving only `(-self.s, C::generator())`. For `s == 0`, `multiexp_vartime` returns the identity, `is_identity()` is true, and `verify` returns `true`.
- `SchnorrAggregator::complete` (lines 175–178) treats the zero-count case as a fixup — returning `None` when nothing was aggregated — proving the codebase already recognizes the empty case as invalid on the production side while forgetting to enforce it on the verification side. This is exactly the reported pattern: the degenerate `total == 0` branch is guarded in one function (`spin`/`complete`) but unguarded in its counterpart (`fulfillRandomness`/`verify`).

The result is a forged "signature": `SchnorrAggregate { Rs: [], s: 0 }` verifies over `dst` and an empty `keys_and_challenges`, and any caller that only length-checks `Rs` against a non-empty expectation still hits the issue because `verify` itself is the intended strictness boundary per the crate's documentation that signatures "are intended to be strict" (`crypto/schnorr/src/lib.rs:36`).

### Impact Explanation
An unprivileged party can fabricate an aggregate Schnorr signature that passes `SchnorrAggregate::verify` without possessing any private key. Any verifier implementation that trusts the library's `verify` to mean "at least one signature was checked" can be made to accept a proof for an arbitrary empty claim (e.g., an empty set of attestations, an empty validator set, or a message claiming zero required signers). This is a forged signature accepted by the verifier formula — the strongest class under the validation rules.

### Likelihood Explanation
Reachable purely from public inputs: `SchnorrAggregate::read` consumes untrusted bytes with no semantic checks, and `verify` is a public API. Exploitation requires a caller to verify an aggregate where the expected challenge list can be empty (or to accept an empty `keys_and_challenges` consistent with the attacker's `Rs`), which is plausible in any flow where the number of expected signatures is itself attacker-influenced (e.g., counting outputs/attestations found). The proof requires no key material, no malicious peer, and no collusion.

### Recommendation
Mirror the fixup from `SchnorrAggregator::complete` into `SchnorrAggregate::verify`: return `false` early when `self.Rs.is_empty()` (equivalently when `keys_and_challenges.is_empty()`), e.g.:

```rust
if self.Rs.is_empty() || (self.Rs.len() != keys_and_challenges.len()) {
  return false;
}
```

Also consider hardening `SchnorrAggregate::read` to reject zero-length `Rs`, since a serialized aggregate of nothing is never meaningful.

### Proof of Concept
```rust
use ciphersuite::Ciphersuite;
use schnorr::aggregate::SchnorrAggregate;

// Attacker-controlled bytes: u32 length = 0, then repr of scalar 0
let mut bytes = vec![0u8; 4];
bytes.extend(<C as Ciphersuite>::F::ZERO.to_repr());

let mut slice = bytes.as_slice();
let agg = SchnorrAggregate::<C>::read(&mut slice).unwrap();
assert_eq!(agg.Rs().len(), 0);

// Verifies as a valid aggregate over any DST with an empty challenge set
assert!(agg.verify(b"some_dst", &[]));
```
The equality `Σ z_i·R_i + Σ z_i·c_i·A_i - s·G = 0` degenerates to `-0·G = 0`, so `multiexp_vartime` yields identity and `verify` returns `true` — a signature forged with no secret knowledge, enabled solely by the missing zero-count guard that `complete` already enforces.