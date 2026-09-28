### Title
Aggregate Schnorr signature forgery via predictable aggregation weights — attacker chooses `Rs`/`s` after learning all weights ([File: crypto/schnorr/src/aggregate.rs])

### Summary
`SchnorrAggregate::verify` derives the per-signature weight `z_i` only from the submitted challenges (`keys_and_challenges`), never binding the public keys `A_i` or the nonce commitments `R_i` into the weight transcript. Because every `z_i` is deterministic and publicly computable before the attacker commits to `Rs` and `s`, an unauthenticated attacker can forge an aggregate signature over any set of honest public keys and challenges — the verification equation degenerates into a linear system the attacker solves freely. This is the same bug class as CVE-2021-26117 (improper authentication): the verification "check" is present but does not actually bind the attacker's supplied values, so it authenticates nothing.

### Finding Description
`verify` (aggregate.rs:127-146) builds the transcript as:

```text
digest = DigestTranscript(dst); domain_separate("signatures")
for each (_, challenge): append_message("challenge", challenge)
z_i = weight(&mut digest)   // sequential, deterministic
check: Σ_i z_i·R_i + Σ_i (z_i·c_i)·A_i + (-s)·G == 0
```

The weight function (aggregate.rs:22-65) hashes only the digest state, which contains exclusively the challenges. Neither `self.Rs[i]` nor `key` is appended to the digest. Contrast with the aggregator side (`SchnorrAggregator::aggregate`, aggregate.rs:169-172), which symmetrically only appends `challenge` — so prover and verifier agree on weights, but the weights are independent of everything the attacker controls in the proof object (`Rs`, `s`).

Consequently, for any target `(A_1..A_n, c_1..c_n)` — including a single honest public key and the challenge the attacker wants forged — the attacker computes `z_1..z_n` locally, picks arbitrary `s`, sets `R_i = r_i·G - c_i·A_i` for `i ≥ 2` with random `r_i`, and sets `R_1 = z_1^{-1}·(s - Σ_{i≥2} z_i·r_i)·G - c_1·A_1`. The multiexp sums to `s·G - s·G = 0` and `verify` returns `true`.

For the minimal case `n = 1`, this reduces to `R = z^{-1}·s·G - c·A` — a forged signature under any public key `A` for any challenge `c`, with zero secret knowledge.

### Impact Explanation
Any caller that accepts `SchnorrAggregate` (readable from untrusted bytes via `SchnorrAggregate::read`, aggregate.rs:77-88) as evidence of `n` valid Schnorr signatures accepts a forgery. An attacker produces bytes that pass `verify` for public keys they do not control and messages they never had signed — concrete signature forgery reachable purely from public inputs. The severity is High: complete authentication bypass of the aggregate signature scheme for all `n ≥ 1`.

### Likelihood Explanation
Reachability requires only that a verifier deserializes an attacker-supplied `SchnorrAggregate` and calls `verify` with its own `keys_and_challenges`. Both are public-input-driven. No collusion, no malicious validator, no leaked keys, and no misuse beyond calling the documented `verify` API are needed. The forgery is fully deterministic (all `z_i` computable offline), requiring no interaction and succeeding with probability 1.

### Recommendation
Bind the full statement into the weight transcript per the half-aggregation paper (eprint 2021/350 §4): before drawing `z_i`, append each `R_i`, each public key, and each challenge to the digest — e.g., in `verify`, `append_message(b"R", self.Rs[i].to_bytes())` and `append_message(b"key", key.to_bytes())`, and mirror the same ordering in `SchnorrAggregator::aggregate`/`complete` so honest aggregation still verifies. With weights bound to the attacker-controlled `Rs`, `z_1` is no longer known when `R_1` is chosen, restoring the Schnorr binding property.

### Proof of Concept
```text
// n = 1 forgery against SchnorrAggregate::<C>::verify
// Given target public key A and challenge c (as the verifier will pass in):

// 1. Locally reproduce the verifier's weight transcript:
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
digest.append_message(b"challenge", c.to_repr());
let z = weight::<_, C::F>(&mut digest);   // fully deterministic, no secret input

// 2. Pick any s and compute the forged nonce commitment:
let s: C::F = random();
let R = C::generator() * (s * z.invert().unwrap()) - (A * c);

// 3. Serialize SchnorrAggregate { Rs: vec![R], s } and send to verifier.
//    Verifier computes pairs: (z, R), (z*c, A), (-s, G)
//    Sum = z·R + z·c·A - s·G = z·(z⁻¹·s·G - c·A) + z·c·A - s·G = 0  → verify() == true
```

Root cause is at crypto/schnorr/src/aggregate.rs:127-146 (verification) and 22-65 / 134-136 / 169-172 (weight derivation omitting `Rs` and keys).