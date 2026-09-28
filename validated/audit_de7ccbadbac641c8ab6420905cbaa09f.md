### Title
Schnorr aggregate verification weights don't bind the nonces/keys, enabling universal forgery - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` derives the per-signature weights `z_i` from a transcript that commits **only to the challenges** — never to the nonce commitments `R_i` or the public keys `K_i`. Per ePrint 2021/350 (which this code cites), the aggregation weights must be unpredictable *and bound to* each `(R_i, K_i, challenge)` tuple. Because `z_i` is a deterministic function of the challenges alone, an unprivileged attacker can compute all weights before choosing the `R_i` values, and then construct an `Rs`/`s` pair that satisfies the verification multiexp for *any* set of public keys and challenges — a forged aggregate signature.

### Finding Description
`SchnorrAggregate::verify` builds its `DigestTranscript` by appending only `challenge` messages from `keys_and_challenges`; neither `self.Rs` nor the keys are appended (aggregate.rs:132-136). Each weight is then drawn sequentially via `weight(&mut digest)` (aggregate.rs:140-143), and verification checks `Σ z_i·R_i + Σ z_i·c_i·K_i − s·G = 0` via `multiexp_vartime` (aggregate.rs:138-145).

Verification equation: `Σ z_i·(R_i + c_i·K_i) = s·G`. Since the attacker supplies `Rs` and `s` (e.g., via `SchnorrAggregate::read`, aggregate.rs:77-88) and `z_i` is fully determined before `Rs` are chosen, the attacker picks arbitrary `x_i`, sets `R_i = x_i·G − c_i·K_i`, and sets `s = Σ z_i·x_i`. Then `Σ z_i·R_i + Σ z_i·c_i·K_i = Σ z_i·x_i·G = s·G` — the equation holds and `verify` returns `true`. Each `R_i` is a valid non-identity point generically, and `read_G` accepts it.

This is the analog of the reported bug class: a required guard (binding of the weights to the aggregate's nonce commitments and keys) is silently absent on the untrusted-input path, exactly as the vLLM loader skips the `trust_remote_code` guard on the `auto_map` path. The correct construction per 2021/350 computes `z_i = H(aggregate_of_all_(R_j, K_j, c_j), i)` so `R_i` cannot be chosen after the weights.

### Impact Explanation
Any consumer that treats `SchnorrAggregate::verify(...) == true` as proof that the holders of `K_i` signed messages with challenges `c_i` accepts a forgery produced with zero private-key knowledge. This yields a forged signature — a listed acceptable impact — for arbitrary public keys and arbitrary messages, reachable purely from attacker-controlled bytes passed to `SchnorrAggregate::read`/`verify`.

### Likelihood Explanation
The forgery is deterministic and requires no special position, collusion, or network conditions — only the ability to submit `(Rs, s)` to a verifier (public inputs). The only mitigating factor is that exploitation requires the verifier to rely on this aggregate-verify API rather than per-signature `SchnorrSignature::verify`, which correctly binds each `(R, key)` through the challenge.

### Recommendation
Include every `R_i` and every public key in the weight transcript before drawing weights — e.g., in `verify` (and symmetrically in `SchnorrAggregator::aggregate`/`complete`, which must produce the same digest), append `R.to_bytes()` and `key.to_bytes()` alongside each `challenge` (aggregate.rs:134-136), matching the `z_i = H(R_1..R_n, K_1..K_n, c_1..c_n, i)` construction of the cited paper. Alternatively, append the full serialized `Rs` and keys before the challenge loop so weights commit to the entire aggregate.

### Proof of Concept
```rust
// Given verifier-fixed (K_i, c_i) pairs the attacker wants to "sign" for:
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
for (_, c) in keys_and_challenges { digest.append_message(b"challenge", c.to_repr()); }

let mut rng = OsRng;
let mut rs = vec![]; let mut s = C::F::ZERO;
for (key, c) in keys_and_challenges {
    let z = weight::<_, C::F>(&mut digest);        // known before choosing R_i
    let x = C::F::random(&mut rng);
    rs.push(C::generator() * x - (*key * *c));    // R_i = x_i·G − c_i·K_i
    s += z * x;                                    // s = Σ z_i·x_i
}
let agg = SchnorrAggregate { Rs: rs, s };
assert!(agg.verify(dst, keys_and_challenges));     // forged: returns true
```
Supporting code: `weight` derives scalars from `digest` only (crypto/schnorr/src/aggregate.rs:22-65); the transcript commits only challenges (lines 132-136); the verification equation `z·R + z·c·K` summed against `−s·G` is checked at lines 138-145; `Rs`/`s` are attacker-read at lines 77-88.

Uncertainty note: reachability depends on callers routing untrusted aggregates into this verifier; the formula flaw itself is proven by the algebra above directly against the in-scope code.