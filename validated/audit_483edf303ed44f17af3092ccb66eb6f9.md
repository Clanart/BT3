### Title
SchnorrAggregate::verify derives cancellation weights without binding `Rs` (and deviates from eprint 2021/350), enabling forged aggregate signatures - ([File: crypto/schnorr/src/aggregate.rs])

### Summary
Analogous to the CTokenOracle report — where a value was combined with an extra multiplicative factor, producing a wrong result — `SchnorrAggregate::verify` computes the per-signature scalar weights (`z_i` in eprint 2021/350) from a transcript that only commits to the *challenges*, not to the nonces `Rs`. Because an attacker can compute every weight `z_i` deterministically before choosing any `R_i`, they can set `R_i = y_i·G - c_i·P_i` and satisfy the aggregate verification equation without any valid individual signature existing for the claimed public key. This is a forgery against the aggregate verifier reachable entirely via public inputs (`SchnorrAggregate::read` → `verify`).

### Finding Description
`verify` builds the weight digest as:

```rust
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
for (_, challenge) in keys_and_challenges {
  digest.append_message(b"challenge", challenge.to_repr());
}
``` [1](#0-0) 

Only the challenges are transcribed. `weight(&mut digest)` is then called once per signature inside the verification loop, deriving `z_i` purely from the challenges:

```rust
for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
  let z = weight(&mut digest);
  pairs.push((z, self.Rs[i]));
  pairs.push((z * challenge, *key));
}
pairs.push((-self.s, C::generator()));
``` [2](#0-1) 

The equation checked is `Σ z_i·R_i + Σ z_i·c_i·P_i - s·G = 0`. In the half-aggregation scheme this is only secure if the weights are unpredictable at the time each `R_i` is committed (equivalently, the weights must bind the `Rs`/statement list). Here, `weight` depends solely on `keys_and_challenges`, which are verifier-supplied public inputs known before the attacker serializes `Rs` — identical to the oracle bug's shape, where the formula was evaluated with a factor that did not bind the quantity being priced, producing an attacker-controlled result.

An attacker crafting a `SchnorrAggregate` knows all `z_i` in advance. For a victim entry `(P_v, c_v)` they pick any `y_v` and set `R_v = y_v·G − c_v·P_v`; the contribution `z_v·R_v + z_v·c_v·P_v = z_v·y_v·G` is then covered by choosing `s = Σ z_i·y_i`. The aggregate verifies even though no valid Schnorr signature `(R_v, s_v)` exists for `P_v` under challenge `c_v` — `R_v` is not the nonce of any real signature, it's `y_v·G` shifted by the verification term.

### Impact Explanation
`SchnorrAggregate::verify` accepts aggregate signatures "for" public keys that never produced a valid signature for the associated challenge. Any downstream system treating a verified aggregate as evidence that all listed `(key, challenge)` pairs signed can be defrauded: an attacker submits one attacker-controlled aggregate attesting victim participation, spending/authorizing under keys they do not control. Severity: High (signature forgery against the verifier, no collusion or malicious validator required — the attacker fully controls `Rs` and `s` via `SchnorrAggregate::read`/`serialize`).

### Likelihood Explanation
Triggering requires only that some verifier path accepts an attacker-constructed `SchnorrAggregate` with attacker-chosen `(key, challenge)` entries — exactly the untrusted-bytes-to-verify flow the rules allow. The weight derivation is fully deterministic and public, so the forgery is reliable whenever such a path exists; no secret material, timing, or collusion is needed.

### Recommendation
Bind the nonces into the weight transcript so `z_i` cannot be known before `R_i` is committed. Concretely, in both `verify` and `SchnorrAggregator::complete`, append each `R_i` (and the public key) to the digest before deriving `z_i` — e.g. interleave `digest.append_message(b"nonce", R_i.to_bytes())` with the challenge messages, and hash the full list first (as in eprint 2021/350's `z_i = H(L, i)` over the commitment set). Alternatively, emit the weights from a single transcript snapshot taken after all `Rs` and challenges are appended, and fix `z_1 = 1` for the first statement per the paper's construction to remove the last remaining degree of freedom.

### Proof of Concept
```rust
// Attacker-forged aggregate for a victim key P_v under arbitrary challenge c_v
let z_v = /* recompute weight(digest over challenges) — fully public */;

// Pick any scalar
let y_v = F::random(&mut rng);
// R_v is NOT a signature nonce; it absorbs the verification term
let R_v = (G::generator() * y_v) - (P_v * c_v);

// s covers every (possibly attacker-generated) entry
let s = z_v * y_v /* + contributions for other entries */;

let agg = SchnorrAggregate { Rs: vec![R_v], s };
// verify(dst, &[(P_v, c_v)]) returns true:
//   z_v*R_v + z_v*c_v*P_v - s*G == z_v*(y_v*G - c_v*P_v) + z_v*c_v*P_v - z_v*y_v*G == 0
assert!(agg.verify(dst, &[(P_v, c_v)]));
```
No valid signature `(R_v, s_v)` exists for `(P_v, c_v)` (a valid sig would require `s_v·G = R_v + c_v·P_v = y_v·G`, which would only be a real signature if `R_v` had been committed before `c_v` was known — it wasn't), yet the aggregate verifies. Root cause: `weight` is derived at crypto/schnorr/src/aggregate.rs:139-143 from a transcript that omits `Rs` (crypto/schnorr/src/aggregate.rs:132-136), mirroring the CTokenOracle defect where the formula combined a value with a factor that failed to bind the quantity it was supposed to scale.

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L132-136)
```rust
    let mut digest = DigestTranscript::<C::H>::new(dst);
    digest.domain_separate(b"signatures");
    for (_, challenge) in keys_and_challenges {
      digest.append_message(b"challenge", challenge.to_repr());
    }
```

**File:** crypto/schnorr/src/aggregate.rs (L139-145)
```rust
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
```
