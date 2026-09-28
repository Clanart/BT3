### Title
`DLEqProof::verify` / `MultiDLEqProof::verify` fail open on an empty statement list — a forged proof verifies when the DLEq boundary is vacuous - (File: crypto/dleq/src/lib.rs)

### Summary
The external report describes a fail-open pattern: a security boundary that cannot be enforced is silently replaced by a path that enforces nothing, while still reporting success. Serai exhibits the same class inside its DLEq verifier. `DLEqProof::verify` (crypto/dleq/src/lib.rs:160-180) and `MultiDLEqProof::verify` (crypto/dleq/src/lib.rs:264-294) only require that the embedded challenge scalar `self.c` equals the Fiat-Shamir challenge computed over the transcript. When the `generators`/`points` slices are empty (or an inner series is empty in the multi-proof case), no `verify_statement` call binds any generator, nonce recomputation, or point to the transcript. Verification degenerates to checking `c == challenge(domain_separator_only)`, a value any party can compute with no secret — the verifier "succeeds" while enforcing nothing, exactly the fail-open shape of the PraisonAI bug.

### Finding Description
- `DLEqProof::verify` checks `generators.len() == points.len()`, domains-separates with `b"dleq"`, then iterates `zip` over the slices (crypto/dleq/src/lib.rs:166-173). With length-0 slices the loop body never runs; no `verify_statement` transcripts `sG - cA` recomputation occurs.
- It then accepts iff `self.c == challenge(transcript)` (crypto/dleq/src/lib.rs:175). The transcript contains only the `b"dleq"` domain separator, so the required `c` is a public, deterministic constant.
- `MultiDLEqProof::verify` has the same structure (crypto/dleq/src/lib.rs:270-289): the per-series loop `generators.iter().zip(points)` silently does nothing for an empty inner `Vec`, and `points.len() == generators.len() == 0` passes the outer length checks.
- `DLEqProof::read` / `MultiDLEqProof::read` (crypto/dleq/src/lib.rs:191-193, 308-315) deserialize `c` and `s` from untrusted bytes, so the forged `c` is fully attacker-supplied.
- The identical vacuous-truth pattern exists in `BatchVerifier::verify`/`verify_vartime` (crypto/multiexp/src/batch.rs:93-101): an empty queued statement set makes `multiexp(&[])` the identity, returning `true`. Together these form a fail-open verification surface rather than a fail-closed one.

### Impact Explanation
A DLEq proof is the binding mechanism Serai uses to prove two points share a discrete logarithm across generators (used, e.g., in DKG key/generator promotion paths where `ThresholdKeys`/`Commitments` are promoted between generator contexts). If any in-scope caller constructs the `generators`/`points` lists from deserialized or filtered untrusted data such that they can be empty — or a `MultiDLEqProof` inner series can be empty — an attacker can submit `(c = challenge_of_empty_transcript, s = arbitrary)` via `DLEqProof::read`/`MultiDLEqProof::read` and have `verify` return `Ok(())`, i.e., a forged proof asserting equality of nothing is accepted as a valid proof of key equality. This is a forged-proof acceptance resulting from the verifier failing open when the statement set it should enforce is absent.

### Likelihood Explanation
Exploitability requires a call site where the statement list length is attacker-influenced (e.g., derived from a deserialized `Commitments`/`EncryptedMessage` payload or a filtered iterator). I could not fully trace every in-scope caller (dkg/promote, pedpop share verification) within the available iterations to confirm an attacker-emptiable list. As written, passing a statically empty list would be integrator misuse; the vulnerability is concrete at the verifier level — a verifier that accepts a computable challenge over a vacuous transcript rather than rejecting zero statements is an incorrect verifier formula. Severity: Medium pending confirmation of a reachable empty-list call site; High where such a path exists in the DKG promotion flow.

### Recommendation
Fail closed on empty statement sets, mirroring the advisory's fix:
1. In `DLEqProof::verify`, return `Err(DLEqError::InvalidProof)` when `generators.is_empty()` (and require `generators.len() == points.len() != 0`).
2. In `MultiDLEqProof::verify`, reject an empty outer list and reject any empty inner `generators`/`points` series before the zip loop.
3. In `BatchVerifier::queue`, reject empty `pairs` iterators; in `verify`/`verify_vartime`, treat an empty verifier as failure or document/assert non-emptiness, so "nothing was enforced" can never report success.
4. Add regression tests: empty statement lists must fail verification, and an empty `BatchVerifier` must not report success.

### Proof of Concept
```rust
use transcript::{Transcript, RecommendedTranscript};
use dleq::{DLEqProof, challenge};
use dalek_ff_group::Ed25519;
use zeroize::Zeroizing;

// Any transcript implementing the Transcript trait
let mut transcript = RecommendedTranscript::new(b"forged-empty-dleq");

// Compute the challenge the verifier will expect for an empty statement set
let mut verifier_transcript = transcript.clone();
verifier_transcript.domain_separate(b"dleq");
let c = challenge::<_, <Ed25519 as Ciphersuite>::F>(&mut verifier_transcript);

// Forge a proof: s can be anything since no statement binds it
let forged = DLEqProof { c, s: <Ed25519 as Ciphersuite>::F::ZERO };

// Attacker-influenced empty statement lists
let generators: &[<Ed25519 as Ciphersuite>::G] = &[];
let points: &[<Ed25519 as Ciphersuite>::G] = &[];

// This returns Ok(()) — the proof "verifies" despite asserting nothing
assert!(forged.verify(&mut transcript, generators, points).is_ok());
```
(`DLEqProof` fields are private; the equivalent forgery is submitted through `DLEqProof::read` by serializing `c || s` with `c` set to the empty-transcript challenge, which requires no secret knowledge.)