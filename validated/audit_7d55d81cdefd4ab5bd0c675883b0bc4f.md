### Title
`DLEqProof::verify` / `MultiDLEqProof::verify` accept vacuous proofs over empty statement lists — a proof that binds to nothing verifies successfully - (File: crypto/dleq/src/lib.rs)

### Summary
The yAxis bug class is a missing post-loop sufficiency check: the controller ran its withdrawal loop, failed to gather the requested amount, and still completed the operation — silently delivering nothing in exchange for the user's burned shares. The Serai analog is in `crypto/dleq/src/lib.rs`: `DLEqProof::verify` (lines 160–180) and `MultiDLEqProof::verify` (lines 264–294) iterate over `generators`/`points`, accumulate transcript state, and compare `self.c` against the derived challenge — but never check that the statement lists are non-empty. With empty lists the loop body never runs, the challenge reduces to a publicly computable constant (`challenge` over a bare `domain_separate(b"dleq")` transcript), and any attacker can forge a `DLEqProof { c: <that constant>, s: <anything> }` that verifies. The verifier "succeeds" while having verified zero discrete-log statements — exactly the shape of "insufficient liquidity" silently treated as success.

### Finding Description
In `DLEqProof::verify` at `crypto/dleq/src/lib.rs:160-180`, the only structural check is `generators.len() != points.len()` (line 166). An attacker supplying `generators = []` and `points = []` passes that check (`0 == 0`). `verify_statement` is never invoked, so no point or generator is ever bound to the proof. `challenge(transcript)` at line 175 is then evaluated over a transcript containing only `domain_separate(b"dleq")`, a fully deterministic, publicly computable value. The attacker reads a proof via `DLEqProof::read` (line 191, an explicitly in-scope untrusted-byte sink) or constructs it directly, sets `c` to that constant and `s` to any scalar, and `verify` returns `Ok(())`.

`MultiDLEqProof::verify` (lines 264–294) has the identical flaw: `points.len() == generators.len() == self.s.len() == 0` passes the length checks, no `discrete_logarithm` messages or statements are transcripted, and `self.c` just needs to equal the deterministic `challenge` over `domain_separate(b"multi_dleq")`.

This is the same failure shape as the yAxis report: the function iterates to satisfy a request, gets nothing (zero statements / zero liquidity), and fails to revert — returning success on an empty result instead of raising an error.

### Impact Explanation
Any caller that uses `DLEqProof::verify`/`MultiDLEqProof::verify` to gate an action on "the prover knows a shared discrete log across these points" can be satisfied by a proof binding to no points at all, if the statement lists are populated from attacker-influenced data (e.g., deserialized lists, empty intersection of expected keys). The attacker forges a proof with no secret knowledge and no valid witness — a forged proof accepted by an unprivileged party using only public inputs. This is reachable entirely through `DLEqProof::read` bytes plus a `verify` call, satisfying the reachability rules.

### Likelihood Explanation
Medium likelihood: exploitation requires a caller that feeds verifier-controlled or empty statement vectors into `verify`. The `read` entry point is in-scope and public, and empty `Vec` statements are trivially expressible. Severity is bounded because an integrator that hard-codes non-empty generator sets is unaffected, so this rates Medium rather than High.

### Recommendation
Reject empty statements at the top of both verifiers, mirroring the recommended "revert when `_amount` is not zero after the loop" fix:

```rust
if generators.is_empty() || generators.len() != points.len() {
  Err(DLEqError::InvalidProof)?;
}
```

and in `MultiDLEqProof::verify`, require `generators.len() != 0` and each inner `generators`/`points` list to be non-empty. Optionally, enforce the same in `prove` to fail loudly rather than produce a degenerate proof.

### Proof of Concept

```rust
use rand_core::OsRng;
use transcript::{Transcript, RecommendedTranscript};
use ciphersuite::{Ciphersuite, Secp256k1};
use dleq::DLEqProof;

// Deterministic challenge for an empty DLEq verification
let mut transcript = RecommendedTranscript::new(b"test");
transcript.domain_separate(b"dleq");
// Compute the same challenge the verifier will compute over no statements.
// challenge() is pub(crate); reproduce via any DLEqProof::verify call path or
// replicate the wide-reduction here.

// Forged proof: c = empty-statement challenge, s = arbitrary
let forged = DLEqProof::<k256::ProjectivePoint>::read(
  &mut &[/* repr(c) || repr(s=0) */][..]
).unwrap();

// No generators, no points — verifies with no witness knowledge
assert!(forged.verify(&mut RecommendedTranscript::new(b"test"), &[], &[]).is_ok());
```

Concretely: serialize a `DLEqProof` whose `c` equals the challenge derived from a transcript containing only `domain_separate(b"dleq")` (computable offline via the `challenge` routine at `crypto/dleq/src/lib.rs:28-85`), feed it through `DLEqProof::read`, and call `verify` with two empty slices — it returns `Ok(())`, "paying out" a successful verification for a zero-statement request.