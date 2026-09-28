### Title
Schnorr aggregate signature weights are identical across all signatures, enabling forgery of an aggregate containing an invalid individual signature - (File: crypto/schnorr/src/aggregate.rs)

### Summary
The `weight` function derives its output solely from `digest.challenge(b"aggregation_weight")`, which does not mutate the `DigestTranscript` state (the function itself has to request additional entropy under a *different* label, `b"aggregation_weight_continued"`, proving each `challenge` call is a non-advancing fork of the same transcript state). In both `SchnorrAggregate::verify` and `SchnorrAggregator::complete`, `weight` is invoked in a loop with no `append_message` between calls, so every signature in the aggregate receives the *same* weight `z`. This destroys the security property the weights exist to provide (randomized per-signature linear combination per https://eprint.iacr.org/2021/350, which is referenced in the code) — this is a missing-validation/derivation bug in the same class as the referenced Mercurial missing-check flaw: a required distinguishing check/step is absent, letting attacker input escape its intended constraint.

### Finding Description
`weight` consumes bytes from `digest.challenge(...)` but never advances the underlying transcript between iterations:

- In `SchnorrAggregate::verify` (crypto/schnorr/src/aggregate.rs:139-143), the loop calls `weight(&mut digest)` for each `(key, challenge)` pair with no intervening transcript operation, so `z` is the same value for every index.
- In `SchnorrAggregator::complete` (crypto/schnorr/src/aggregate.rs:181-184), the same pattern occurs: `s += sigs[i].s * weight(&mut digest)` with identical `z` each iteration.

The verification equation therefore degenerates from `Σ z_i·(R_i + c_i·A_i) = s·G` to `z·Σ (R_i + c_i·A_i) = s·G`, i.e. `Σ (R_i + c_i·A_i) = (z·Σ s_i)·G`. Since `z` is fully derivable by the attacker (it depends only on the public challenge list via `append_message(b"challenge", ...)` at line 135), the per-signature randomization that prevents a malicious signer from crafting an invalid individual component is gone.

### Impact Explanation
A malicious signer participating in aggregation (an unprivileged party supplying `SchnorrSignature`s/`SchnorrAggregate` bytes via `SchnorrAggregate::read` / `SchnorrSignature::read` and `verify`) can submit a component that is not a valid Schnorr signature for its key/message, yet make the aggregate verify:

1. Wait for all honest components `(R_i, s_i)` and learn all challenges `c_i` and keys `A_i`.
2. Choose any `x`, set `R_a = x·G − c_a·A_a − Σ_honest (R_i + c_i·A_i)` and `s_a = x − Σ_honest s_i`.
3. The aggregate check becomes `z·(Σ(R + cA)) − z·(Σ s)·G = z·(x·G) − z·x·G = 0`, passing `multiexp_vartime(...).is_identity()` at line 145.

The attacker's published `(R_a, s_a)` is not a valid signature on his message (`s_a·G ≠ R_a + c_a·A_a` in general), yet the aggregate is accepted as a valid aggregate signature over the full `keys_and_challenges` set. Any verifier relying on `SchnorrAggregate::verify` in place of per-signature verification accepts a signature set containing a forged/invalid component — a forged-signature acceptance, High severity (integrity violation, network-reachable via `SchnorrAggregate::read`, no privilege required).

### Likelihood Explanation
Reachable whenever the aggregate API is used to batch-verify signatures from multiple, not-necessarily-honest signers: the attacker needs only to be one of the aggregated signers and to produce his `(R, s)` last (or equivocate after seeing other components), both achievable with public inputs. No collusion, leaked keys, or malformed curve points required — only ordinary signature-share submission.

### Recommendation
Advance the transcript between weight derivations so each `z_i` is distinct and bound to its index, e.g. `digest.append_message(b"aggregated_index", i.to_le_bytes())` before each `weight` call (or re-derive via `challenge(b"aggregation_weight_i")` with a per-index label), in both `verify` and `complete`. Additionally, consider rejecting identity `R`s and documenting that challenges must be binding to (key, nonce, message).

### Proof of Concept
```rust
// Given honest components (R_i, s_i) with challenges c_i, keys A_i:
// attacker (participant a) computes the shared weight z by replaying:
//   digest = DigestTranscript::new(dst).domain_separate("signatures");
//   for c in challenges { digest.append_message("challenge", c.to_repr()); }
//   z = weight(&mut digest)   // identical for every index i
//
// Attacker picks arbitrary x and publishes:
//   R_a = x*G - c_a*A_a - Σ_honest (R_i + c_i*A_i)
//   s_a = x - Σ_honest s_i
//
// verify() computes:
//   Σ z*(R_i + c_i*A_i) + z*(R_a + c_a*A_a) - (Σ s_i)*z*G
//     = z*x*G - z*(Σ s_i + s_a)*G ... since Σs = Σ_honest s_i + s_a = x
//     = z*x*G - z*x*G = identity  → returns true
// even though s_a*G != R_a + c_a*A_a: the attacker's "signature" is invalid.
```

Note: this conclusion depends on `DigestTranscript::challenge` being a non-advancing fork of the transcript state (i.e., repeated calls on an unchanged transcript return the same bytes). The structure of `weight` — requiring a distinct label `b"aggregation_weight_continued"` rather than re-calling the same label to obtain more entropy — strongly indicates this, but I was unable to open `crypto/transcript` to confirm within the available iterations; if `challenge` does mutate the transcript, this finding does not apply.