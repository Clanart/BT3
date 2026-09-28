Let me check the Schnorr batch verification and Bitcoin key negation handling.### Title
Schnorr half-aggregation weights are not bound to nonces/keys, enabling universal aggregate-signature forgery - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` recomputes the per-signature weights `z_i` from a transcript that commits **only to the challenges** (`digest.append_message(b"challenge", ...)`), never to the nonce commitments `Rs` nor the public keys. Because the weights are therefore publicly computable *before* the attacker chooses `R_i`, anyone can forge a `SchnorrAggregate` that verifies for an arbitrary set of public keys and challenges without knowing any secret key. This violates the binding requirement of the half-aggregation scheme (ePrint 2021/350), which requires each weight to commit to the signatures being aggregated.

### Finding Description
In `SchnorrAggregate::verify` (crypto/schnorr/src/aggregate.rs:127-146):

```rust
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
for (_, challenge) in keys_and_challenges {
  digest.append_message(b"challenge", challenge.to_repr());
}
// weights z_i derived via weight(&mut digest)
pairs.push((z, self.Rs[i]));
pairs.push((z * challenge, *key));
pairs.push((-self.s, C::generator()));
```

The verification equation is `Σ z_i·R_i + Σ z_i·c_i·A_i − s·G == 0`, i.e., `Σ z_i·(R_i + c_i·A_i) == s·G`.

The digest commits to `dst`, the `"signatures"` separator, and the challenges only. `self.Rs` and the public keys are never transcripted into `digest` before `weight()` is called. `weight()` (aggregate.rs:22-65) deterministically reduces the challenge bytes to a scalar, so the full weight vector `(z_1, …, z_n)` is computable by anyone given only `(dst, challenges)` — both known before the attacker constructs the aggregate.

Since `z_i` does not depend on `R_i`, the attacker can choose each `R_i` as a function of `z_i` and the target key `A_i`, fully satisfying the equation with known discrete logs only.

Note the aggregator side (`SchnorrAggregator::complete`, aggregate.rs:175-186) uses the same challenge-only transcript, so honest aggregation and verification stay consistent — the flaw is purely the missing binding in the weight derivation, not an honest/malicious mismatch.

### Impact Explanation
Forgery of an aggregate Schnorr signature for any set of `(public_key, challenge)` pairs chosen by the verifier, with zero knowledge of secret keys. Any protocol accepting `SchnorrAggregate` (deserializable via `SchnorrAggregate::read`, aggregate.rs:77-88) as authentication over previously signed messages can be bypassed by an unprivileged party who only observes the public keys and the challenges/messages. This is a complete break of the unforgeability property the aggregation scheme exists to preserve.

### Likelihood Explanation
Exploitation requires only: (a) a verifier that calls `SchnorrAggregate::verify` on attacker-supplied bytes, and (b) knowledge of the `dst` and the `(key, challenge)` list — all public inputs. The forgery is deterministic, algebraic, and works with probability 1; no brute force, collision search, or signer cooperation is needed. Wherever this aggregate verification is reachable, forgery is trivially achievable.

### Recommendation
Bind each weight to the full signature context. Before deriving `z_i`, append `Rs[i]` and the public key to the digest, matching the paper's `z_i = H(aggregate_data, R_i, pk_i, m_i)` construction — e.g., transcript `R_i.to_bytes()` and `key.to_bytes()` inside the per-item loop, or commit to the whole `Rs`/keys vector up front, in both `verify` and `SchnorrAggregator::aggregate`/`complete` so the two sides stay consistent. Alternatively, force the first weight to `F::ONE` and derive only `z_2..z_n` from a transcript that still binds all `Rs` and keys. Keep the continued-challenge mechanism in `weight()` to preserve the low bias.

### Proof of Concept
Let the verifier call `verify(dst, keys_and_challenges)` with `n` pairs `(A_i, c_i)`.

1. Recompute the weights locally: build `DigestTranscript::<C::H>::new(dst)`, `domain_separate(b"signatures")`, append each `c_i` as `b"challenge"`, then call `weight()` `n` times → `(z_1, …, z_n)`. All inputs are public.
2. Choose arbitrary scalars `x_i` (e.g., `x_i = i`). For each `i`, set:
   - `R_i = x_i·G − c_i·A_i`  (a group operation on public values)
3. Set `s = Σ z_i·x_i`.
4. Submit `SchnorrAggregate { Rs: [R_1..R_n], s }` (serialize via `write`, parseable by `read`).

Verification computes `Σ z_i·R_i + z_i·c_i·A_i − s·G = Σ z_i·(x_i·G − c_i·A_i + c_i·A_i) − (Σ z_i·x_i)·G = 0`, i.e., the identity — the forged aggregate passes `multiexp_vartime(&pairs).is_identity()`. No secret key was used. A minimal Rust test can be built by generating a key pair, computing any challenge `c` via a transcript, running the steps above, and asserting `verify` returns `true`.