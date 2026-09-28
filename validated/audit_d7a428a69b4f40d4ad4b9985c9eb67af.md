### Title
Forged `SchnorrAggregate` accepted: aggregation weights bind only to challenges, not to `Rs` or public keys - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` is supposed to verify a half-aggregated Schnorr signature (eprint 2021/350): given `(R_i, s_i)` signatures under keys `A_i` with challenges `c_i`, it checks `Σ z_i·R_i + Σ z_i·c_i·A_i − s·G == 0`, where `z_i` are per-signature weights meant to prevent malleability/rogue cancellation. The weights are derived from a `DigestTranscript` seeded with only the caller's DST plus the challenges — the attacker-controlled `Rs` and the public keys are never transcripted into the weight derivation.

### Finding Description
In `verify`, the transcript is built as:

```rust
// crypto/schnorr/src/aggregate.rs:132-143
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
for (_, challenge) in keys_and_challenges {
  digest.append_message(b"challenge", challenge.to_repr());
}
...
let z = weight(&mut digest);
pairs.push((z, self.Rs[i]));
pairs.push((z * challenge, *key));
pairs.push((-self.s, C::generator()));
```

Because `weight()` depends only on `dst` and the challenge list — all known publicly before the aggregate is formed — every `z_i` is a deterministic, attacker-known scalar. The verification equation is:

`Σ_i z_i·(R_i + c_i·A_i) − s·G = 0`

An untrusted party supplying bytes to `SchnorrAggregate::read` (crypto/schnorr/src/aggregate.rs:77-88) can set `R_i = −c_i·A_i` (a publicly computable point, no discrete log needed) for every `i`, and set `s = 0`. Each term `z_i·(R_i + c_i·A_i)` vanishes and the sum is the identity, so `multiexp_vartime(&pairs).is_identity()` returns true. The aggregate "verifies" without any valid constituent signature existing — a complete forgery against any set of keys/challenges.

In the correct half-aggregation construction the weight `z_i` must bind `R_i`, `A_i`, and the message/challenge, precisely so that `R_i` cannot be chosen after the weight is fixed. This implementation binds none of them, which is the exact analog of the reported bug class: attacker-controlled elements (`Rs`, `s` read via `read_G`/`read_F`) are fed into a verification "query" whose parameters (the `z_i` weights) fail to incorporate them, letting injected values satisfy the equation.

### Impact Explanation
Any consumer calling `SchnorrAggregate::verify` on an aggregate read from untrusted bytes will accept a fabricated aggregate signature for arbitrary public keys and challenges. Where half-aggregation is used to compress/attest threshold or validator signatures, this yields universal forgery: a single malicious party, with no key shares and no cooperation, produces an "aggregate" that passes verification. This is a forged-signature-accepted outcome — the strongest acceptance criterion for an analog.

### Likelihood Explanation
Reachable by an unprivileged party: the only inputs are the aggregate bytes (`SchnorrAggregate::read`, canonical-checked but otherwise unconstrained points/scalar) and the public `keys_and_challenges` list. The `z_i` are fully predictable since they depend only on public data, so the forgery is deterministic — not probabilistic. Exploitation requires only that some verifier uses the `aggregate` feature path. Caveat: practical impact is bounded by whether deployed systems actually call `SchnorrAggregate::verify` with attacker-supplied aggregates; the crate documents it as supported functionality, so the honest assessment is High severity where used, contingent on that usage.

### Recommendation
Bind every per-signature element into the weight transcript: for each `i`, append `Rs[i].to_bytes()`, `key.to_bytes()`, and the challenge before drawing `z_i = weight(&mut digest)` (matching the paper, where `z_i = H(agg-info, R_i, A_i, m_i)`). Correspondingly, `SchnorrAggregator::aggregate` must append the signature's `R` and public key alongside `challenge` so prover and verifier derive identical weights. Also reject `s == 0` / identity `R_i` defensively.

### Proof of Concept
```rust
// Attacker knows dst and keys_and_challenges = [(A_i, c_i)].
// Forgery:
let mut agg_bytes = Vec::new();
let n = keys_and_challenges.len() as u32;
agg_bytes.extend(n.to_le_bytes());
for (A_i, c_i) in keys_and_challenges {
  let R_i = -(*c_i * A_i);           // point arithmetic, no DL needed
  agg_bytes.extend(R_i.to_bytes());  // canonical encoding of a valid point
}
agg_bytes.extend(C::F::ZERO.to_repr()); // s = 0

let agg = SchnorrAggregate::<C>::read(&mut agg_bytes.as_slice()).unwrap();
assert!(agg.verify(dst, keys_and_challenges)); // returns true: forged aggregate accepted
```

Each verification term becomes `z_i·(R_i + c_i·A_i) = z_i·0 = 0`, and `−s·G = 0`, so the multiexp sums to identity and `verify` returns `true` — no valid underlying signature was ever produced.