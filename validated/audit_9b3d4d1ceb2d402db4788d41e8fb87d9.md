### Title
`SchnorrAggregate::verify` accepts forged aggregate signatures because aggregation weights do not bind the nonces/public keys — (File: crypto/schnorr/src/aggregate.rs)

### Summary
Analogous to CVE-2023-40020 — where an authorization check nominally runs yet fails to actually gate the protected action — `SchnorrAggregate::verify` nominally performs a multi-signature verification yet the randomizing weights that are supposed to make the batch equation non-trivial are derived solely from the caller-supplied challenges. An attacker who controls the claimed `(key, challenge)` pairs can compute every weight `z_i` before choosing `R_i`, select nonces that algebraically cancel the unknown discrete logs, and produce a `SchnorrAggregate` that verifies over arbitrary victim public keys without any signature ever being created. This is reachable purely through untrusted bytes via `SchnorrAggregate::read` followed by `verify`.

### Finding Description
`SchnorrAggregate::verify` checks the half-aggregation equation `Σ z_i·R_i + Σ z_i·c_i·A_i − s·G = 0` via `multiexp_vartime`. The weights `z_i` come from `weight::<_, C::F>(&mut digest)`, where `digest` is a `DigestTranscript` seeded with the caller's `dst` and containing only `domain_separate(b"signatures")` plus each `challenge` scalar — it does **not** append the public keys `A_i` or the nonces `R_i`.

Because the `keys_and_challenges` list is fully attacker-controlled and `z_i` is a deterministic public function of `dst || challenges`, the adversary knows all weights before committing to `Rs`. They then choose `R_i = r_i·G − c_i·A_i` for random known `r_i` and set `s = Σ z_i·r_i`. Substitution gives:

```
Σ z_i·R_i + Σ z_i·c_i·A_i = Σ z_i·(r_i·G − c_i·A_i) + Σ z_i·c_i·A_i = Σ z_i·r_i·G = s·G
```

so `multiexp_vartime(&pairs).is_identity()` holds and `verify` returns `true`. The check executes, produces the expected "OK", and the forged aggregate is accepted — the same shape as the report's "403 returned but `next()` still processes the request": the auth gate fires yet nothing is actually authenticated.

The same defect exists on the aggregation side: `SchnorrAggregator::complete` draws weights from a digest that, per `aggregate()`, also only commits to the challenges, so honest aggregation and verification are self-consistent — the forgery lives entirely in the verifier formula. A single-entry aggregate (equivalent to a half-aggregated lone signature) is forgeable identically with `R = r·G − c·A`, `s = z·r`.

### Impact Explanation
Any caller that relies on `SchnorrAggregate::verify` to establish that a set of public keys signed (with challenges `c_i` that bind the keys to messages/nonces per the documented requirement) can be fed a `SchnorrAggregate` — parsed from wire bytes via `SchnorrAggregate::read` — that verifies for arbitrary victim keys and arbitrary claimed challenges/messages, with zero valid signatures involved. This is a complete signature-forgery primitive against the aggregation verifier, violating the core unforgeability property of the half-aggregation construction in eprint 2021/350 (which requires the coefficients to be unpredictable after the statement — including `R_i` and `A_i` — is fixed). Severity: High/Critical, a forged signature reachable from public inputs only.

### Likelihood Explanation
The attack requires no secret, no valid signature, no honest participant, and no special access: the adversary needs only a target public key, a chosen challenge value (e.g., a real Schnorr challenge `H(R‖A‖m)` for whatever message they wish to claim), and arithmetic over public values. Deterministic and fully reliable for every execution.

### Recommendation
Bind the full verified statement into the weight derivation. In both `SchnorrAggregator::aggregate`/`complete` and `SchnorrAggregate::verify`, append each `key.to_bytes()`, each `R_i` (`Rs[i].to_bytes()`), and each challenge to the digest before drawing `weight` — i.e., make `z_i = H(dst, {(A_j, R_j, c_j)}_j, i)` so weights are unpredictable to an adversary who has not yet committed to all nonces. Alternatively, fall back to per-signature `SchnorrSignature::verify` for each entry. Note the aggregate signature format/interop may need a versioning or domain separation change since `read`/`write`/`verify` must agree on the committed statement.

### Proof of Concept
```rust
// Forgery of a SchnorrAggregate for a victim public key the attacker does not control.
// Given: victim key A_v (C::G), attacker-chosen challenge c_v (e.g., a real Schnorr
// challenge for a message they want to attribute to A_v), and the verifier's dst.

let keys_and_challenges = vec![(A_v, c_v)]; // attacker-chosen, public inputs

// Reproduce the verifier's weight derivation (aggregate.rs lines 132-136):
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
digest.append_message(b"challenge", c_v.to_repr());
let z: C::F = weight(&mut digest); // same private fn logic, deterministic and public

// Attacker picks r, sets R so the c_v*A_v term cancels:
let r = C::random_nonzero_F(&mut rng);
let R = C::generator() * r - A_v * c_v;

// s = z * r
let forged = SchnorrAggregate { Rs: vec![R], s: z * r };

// verify computes: z*R + z*c_v*A_v - s*G
//                = z*(r*G - c_v*A_v) + z*c_v*A_v - z*r*G = 0  -> true
assert!(forged.verify(dst, &keys_and_challenges)); // passes with no real signature
```

Root cause in code: the weight digest commits only to challenges — `crypto/schnorr/src/aggregate.rs` — while `Rs` and keys never enter `digest` before `weight` is drawn.