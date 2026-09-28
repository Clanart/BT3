### Title
DLEq proof verifier accepts trivially forgeable proofs when the generator/point lists are empty due to missing length validation — (`crypto/dleq/src/lib.rs`)

### Summary
`DLEqProof::verify` validates that `generators.len() == points.len()`, but never rejects the case where both are empty (and `MultiDLEqProof::verify` accepts per-series empty vectors the same way). When no `(generator, nonce, point)` tuples are transcribed, the Fiat-Shamir challenge is derived solely from the domain separator, so a forged proof with a precomputed `c` and an arbitrary `s` always verifies — the `s` component is never constrained because it only enters the transcript via the computed nonce `sG - cA` inside `verify_statement`, which is never invoked.

### Finding Description
The loop in `DLEqProof::verify` transcribes one statement per `(generator, point)` pair:

```rust
// crypto/dleq/src/lib.rs
transcript.domain_separate(b"dleq");
for (generator, point) in generators.iter().zip(points) {
  Self::verify_statement(transcript, *generator, *point, self.c, self.s);
}
if self.c != challenge(transcript) { Err(DLEqError::InvalidProof)?; }
``` [1](#0-0) 

With `generators = []` and `points = []`, the equality check passes, the loop body executes zero times, and `challenge(transcript)` reduces to a deterministic, publicly computable value over just `domain_separate(b"dleq")` plus whatever the caller already transcribed. An attacker computes `c = challenge(...)` locally — `challenge` is a pure public function of the transcript (`crypto/dleq/src/lib.rs:28`) — serializes `DLEqProof { c, s: <anything> }` via `DLEqProof::read` (`crypto/dleq/src/lib.rs:191`), and the verifier accepts. `s` can be `0`, `1`, or any scalar; it is only bound to the proof through `verify_statement`, which never runs.

The same gap exists in `MultiDLEqProof::verify`: it checks `points.len() == generators.len()` and `self.s.len() == generators.len()` at the outer level, and `points.len() == generators.len()` per series, but a series may still be empty, contributing nothing to the transcript while consuming one `s` element.

### Impact Explanation
This is a forged-proof condition, not merely a crash or DoS. Any caller that feeds attacker-influenced or count-varying generator/point lists into `DLEqProof::verify`/`MultiDLEqProof::verify` can be handed a universally-accepted "proof" attesting to a discrete-log equality that was never demonstrated — e.g., a key-ownership or key-consistency assertion a protocol relies on before crediting or releasing value. This mirrors the CVE-2023-3893 pattern (improper input validation enabling privilege escalation): an unchecked degenerate input causes the security check to silently pass.

Caveat: reachability depends on the caller constructing empty generator lists from attacker-controlled data. In this codebase the fixed-generator callers (FROST nonce binding, PedPoP) use internally fixed generator sets, so a concrete in-tree caller passing an empty list was not verified; the flaw is in the library's failure to reject the degenerate case rather than in a demonstrated call site.

### Likelihood Explanation
`DLEqProof::read` imposes no restrictions beyond canonical scalars, so the forged proof is trivially constructible from public knowledge of the transcript label. Exploitation requires a downstream protocol whose `generators` slice can be empty at verification time — possible whenever the generator count is derived from proof-producer-supplied or session-negotiated data rather than being a protocol constant. Absent such a caller the bug is latent, hence Medium rather than High.

### Recommendation
Reject empty inputs in both verifiers:

```rust
if generators.is_empty() || generators.len() != points.len() {
  Err(DLEqError::InvalidProof)?;
}
```
and in `MultiDLEqProof::verify`, additionally reject empty per-series lists (`generators.is_empty()` / `generators[i].is_empty()`), matching the `assert_eq!` expectations already enforced in `prove`.

### Proof of Concept
```rust
use dleq::DLEqProof;
use transcript::{Transcript, RecommendedTranscript};

// Any PrimeGroup G; transcript seeded identically to the verifier's context.
let mut t = RecommendedTranscript::new(b"ctx");
// Locally reproduce the verifier's challenge over the empty statement list.
let mut forge_t = t.clone();
forge_t.domain_separate(b"dleq");
let c = dleq_challenge(&mut forge_t); // public `challenge()` logic

let forged = DLEqProof { c, s: G::Scalar::ZERO }; // s is never constrained
assert!(forged.verify(&mut t, &[], &[]).is_ok()); // wrongly accepted
```

### Citations

**File:** crypto/dleq/src/lib.rs (L160-180)
```rust
  pub fn verify<T: Transcript>(
    &self,
    transcript: &mut T,
    generators: &[G],
    points: &[G],
  ) -> Result<(), DLEqError> {
    if generators.len() != points.len() {
      Err(DLEqError::InvalidProof)?;
    }

    transcript.domain_separate(b"dleq");
    for (generator, point) in generators.iter().zip(points) {
      Self::verify_statement(transcript, *generator, *point, self.c, self.s);
    }

    if self.c != challenge(transcript) {
      Err(DLEqError::InvalidProof)?;
    }

    Ok(())
  }
```
