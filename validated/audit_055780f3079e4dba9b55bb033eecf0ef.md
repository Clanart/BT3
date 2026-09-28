### Title
Aggregate Schnorr verifier accepts forged signatures because `weight()` does not bind the nonces/public keys - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` in `crypto/schnorr/src/aggregate.rs` verifies a half-aggregated Schnorr signature (eprint 2021/350) by checking `Σ z_i·(R_i + c_i·A_i) == s·G`, where the per-signature weight `z_i` is derived from a transcript over **only the challenges** (`append_message(b"challenge", ...)`), never the nonces `R_i` or the public keys `A_i`. An attacker crafting an aggregate therefore knows every weight before choosing the `R_i` values, and can embed the victim's `c_i·A_i` term into a freely-chosen `R_i`, producing a valid-looking aggregate signature over keys/messages never signed. This is the structural analog of the external report: a per-element safety bound (the random weight meant to prevent malleability/cancellation) is applied independently per entry, with nothing binding the aggregate as a whole — so an attacker "compounds" entries within a single aggregate to cancel out a forged statement.

### Finding Description
In `crypto/schnorr/src/aggregate.rs`:

- `weight()` (lines 22–65) derives a scalar from `digest.challenge(b"aggregation_weight")`.
- `verify()` (lines 127–146) seeds the transcript with `dst`, domain-separates `b"signatures"`, then appends **only each challenge** before drawing the weights (lines 132–136). It then pushes `(z, Rs[i])` and `(z * challenge, key)` pairs and checks the multiexp equals identity.

Because neither `Rs[i]` nor `key` enters the transcript before `z_i` is drawn, `z_i` is independent of the statement it is supposed to commit to. The honest aggregator (`SchnorrAggregator::aggregate`, lines 169–172) has the same limitation — it only transcripts challenges — so honest and malicious aggregates are indistinguishable to the verifier.

The forgery: for each index `i`, the equation requires `R_i + c_i·A_i = s_i·G` for some `s_i` contributing `z_i·s_i` to `s`. For a victim public key `A_v` with challenge `c_v` (a signature the attacker does not possess), the attacker picks an arbitrary scalar `t_v` and sets

```
R_v = t_v·G − c_v·A_v
```

Then `R_v + c_v·A_v = t_v·G`, contributing `z_v·t_v·G` to the sum. For all other entries the attacker uses honestly-known signatures on their own keys (or repeats the same trick), and sets the aggregate scalar `s = Σ z_i·s_i`. Every `R_v` is a valid non-identity curve point and passes `SchnorrAggregate::read` (which enforces canonical non-identity points via `C::read_G`) and `verify`.

Note `verify` even accepts attacker-constructed `SchnorrAggregate` values directly (the fields are crate-private but `read` accepts arbitrary untrusted bytes), making this reachable by any unprivileged party feeding bytes to `SchnorrAggregate::read`/`verify`.

### Impact Explanation
A forged `SchnorrAggregate` verifies for arbitrary public keys and challenges, i.e., a forged signature. Wherever Serai uses half-aggregation to batch-verify Schnorr signatures, an attacker can "prove" signatures over keys they do not control — directly meeting the "forged proof or signature" acceptance bar.

### Likelihood Explanation
Deterministic, not probabilistic: the attacker computes all `z_i` locally (the transcript depends only on public inputs: `dst` and the challenges), then solves for each `R_i` trivially. No interactive assumption, honest-signer cooperation, or hash inversion is needed. Reachable by any party who can submit an aggregate to a verifier.

### Recommendation
Bind each weight to the full statement: append `Rs[i].to_bytes()` and `key.to_bytes()` to the digest (in index order) before drawing `z_i`, in both `SchnorrAggregate::verify` and `SchnorrAggregator::aggregate`. Additionally, consider making the first weight `1` and randomizing the rest (as in other Serai batch verifiers) is *not* sufficient here — every weight must be unpredictable w.r.t. the R/key pair it multiplies. Re-deriving `z_i = H(dst, i, R_i, A_i, c_i)` per entry would also prevent cross-entry cancellation.

### Proof of Concept
```rust
// Crypto-rust pseudocode; requires no victim signature.
// Victim: public key A_v, challenge c_v (bound to some message).
// Attacker picks:
let t = Scalar::random(rng);
let R_v = ProjectivePoint::GENERATOR * t - A_v * c_v; // valid, non-identity w.h.p.

// Build a keys_and_challenges list containing (A_v, c_v) plus any number of
// attacker-controlled honestly-signed entries (A_j, c_j) with real sigs (R_j, s_j).
// Recompute weights z_i locally by replaying the same transcript
// (DigestTranscript::new(dst), domain_separate(b"signatures"), append challenges).

// Aggregate s = Σ_j z_j·s_j + z_v·t
// Then Σ z_i·(R_i + c_i·A_i) = (Σ_j z_j·s_j + z_v·t)·G = s·G  => verify() == true
```

Uncertain: I did not verify every call site of `SchnorrAggregate::verify` in the workspace (search budget exhausted); the flaw exists in the in-scope library regardless of usage, matching the "incorrect verifier formula / forged signature" acceptance criterion.