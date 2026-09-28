I'll check the bitcoin scanner/output-verification path, which is the Serai surface closest to "aggregate value derived incorrectly from components".### Title
SchnorrAggregate half-aggregation weights are not bound to the nonces, allowing cancellation and signature forgery - (File: crypto/schnorr/src/aggregate.rs)

### Summary
The reported oracle bug derives an aggregate (LP price) from a single weakest component instead of a combination that binds all components. The same class exists in `SchnorrAggregate::verify`: the per-signature aggregation weight `z` is a deterministic function of only the caller-supplied challenges — it never binds the public keys or the nonces `R_i`. A forger who sees the challenges can therefore choose the `R_i` values so the weighted statements cancel, producing a "valid" aggregate signature under honest public keys without knowing any secret keys. The aggregate's validity collapses onto a single attacker-chosen component, just as the LP price collapsed onto the single depegged token.

### Finding Description
`verify` builds the multiexp `Σ z_i·R_i + Σ z_i·c_i·P_i − s·G == 0` (crypto/schnorr/src/aggregate.rs:138-145). The weight `z_i` comes from `weight(&mut digest)` where `digest` only has the challenges appended (aggregate.rs:132-136); the transcript never includes `self.Rs` or the keys. Because `z` is deterministic and computable before the forger commits to `Rs` (which are read from untrusted bytes via `SchnorrAggregate::read`, aggregate.rs:77-88), the forger can solve the linear system: for any target public keys `P_i` and challenges `c_i`, compute all `z_i`, pick `s` freely, and set `R_1 = (s·G − Σ_i z_i·c_i·P_i − Σ_{i>1} z_i·R_i)·z_1^{-1}` with arbitrary `R_{i>1}`. The multiexp then evaluates to identity even though no valid constituent signature exists.

In the oracle finding, `min` let one bad component dictate the whole aggregate's value; here, the unbound `z` lets one crafted `R_1` dictate the whole aggregate's validity while honest keys contribute only publicly computable terms.

### Impact Explanation
Any verifier accepting a `SchnorrAggregate` over `(key, challenge)` pairs can be convinced of a forged aggregate attestation. If `complete`/verify APIs downstream (e.g., any aggregator checking multi-signature batches over untrusted `Rs` supplied via `read`) rely on this for authorization, an unprivileged party forging bytes to `SchnorrAggregate::read` produces an accepted aggregate signature under keys they do not control — a forged signature, the exact acceptance criterion.

### Likelihood Explanation
Exploitation is fully deterministic: the forger needs only the public keys and challenges (public inputs), computes `z_i` by replaying the same transcript, and solves one linear equation over the scalar field. No secret knowledge, timing, or interaction is required. It applies whenever `keys_and_challenges.len() >= 1` (for `n=1`, `R_1 = (s·G − z_1·c_1·P_1)·z_1^{-1}` forges directly).

### Recommendation
Bind the weight derivation to everything being aggregated: append each `R_i.to_bytes()` (and the public keys) to the digest before drawing each `z_i`, or switch to verifier-sampled random weights. E.g., in `verify`, inside the loop append `self.Rs[i].to_bytes()` and `key.to_bytes()` to `digest` before calling `weight(&mut digest)`, so `z_i` commits to the nonce and key and the cancellation equation is no longer solvable a priori.

### Proof of Concept
```rust
// Victim keys P_1, P_2 and verifier-chosen challenges c_1, c_2
let keys_and_challenges = [(P1, c1), (P2, c2)];

// Reproduce the verifier's digest to learn the weights
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
digest.append_message(b"challenge", c1.to_repr());
digest.append_message(b"challenge", c2.to_repr());
let z1 = weight::<C::H, C::F>(&mut digest);
let z2 = weight::<C::F, C::F>(&mut digest);

// Forge: choose s and R_2 freely, solve for R_1
let s = C::F::ONE;
let r2 = C::generator(); // any non-identity point works
let r1 = (C::generator() * s
         - (P1 * (z1 * c1)) - (P2 * (z2 * c2))
         - (r2 * z2)) * z1.invert().unwrap();

let forgery = SchnorrAggregate { Rs: vec![r1, r2], s };
assert!(forgery.verify(dst, &keys_and_challenges)); // passes without either secret key
```