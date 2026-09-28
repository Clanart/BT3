### Title
Missing binding of `Rs` in `SchnorrAggregate::verify` enables universal forgery of aggregate signatures - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrSignature::verify` is safe because the caller-supplied challenge binds the signature's `R` (and key/message). The "batch" equivalent, `SchnorrAggregate::verify`, derives per-signature weights `z_i` from a transcript that commits **only to the challenges** — never to the `Rs` vector or the public keys — and fixes no weight to a constant. An attacker who supplies the aggregate (untrusted bytes via `SchnorrAggregate::read` → `verify`) can compute every `z_i` first, then choose each `R_i` and `s` to make the multiexp sum to zero for arbitrary public keys.

### Finding Description
In `verify`, the digest is built as:

```text
digest.domain_separate(b"signatures");
for (_, challenge) in keys_and_challenges {
  digest.append_message(b"challenge", challenge.to_repr());
}
``` [1](#0-0) 

Each weight `z_i = weight(&mut digest)` is therefore a pure function of the challenges, independent of `self.Rs` and of the keys. The verification equation checked is `Σ z_i·R_i + Σ z_i·c_i·A_i − s·G == 0` [2](#0-1) . Because the attacker controls all `R_i` (read at `SchnorrAggregate::read`, aggregate.rs:77-88) and `s`, and knows all `z_i` in advance, they can set `R_i = w_i·G − c_i·A_i` for arbitrary known scalars `w_i` and set `s = Σ z_i·w_i`. Every term then contributes `z_i·w_i·G`, and the equation holds for victim keys `A_i` the attacker does not control.

This is the exact analog of the report: the single-item path is protected by the Fiat–Shamir challenge binding `R`, but the multi-item path drops the corresponding binding — the aggregation transcript must commit to each `(R_i, A_i, challenge)` tuple (or equivalently the aggregator's full input), per eprint 2021/350, so weights cannot be known before `Rs` are fixed.

### Impact Explanation
Any verifier accepting `SchnorrAggregate` from untrusted input accepts a forged aggregate attesting signatures under arbitrary public keys/challenges. This is a forged-signature primitive reachable entirely from public, attacker-controlled bytes.

### Likelihood Explanation
The attack is deterministic and trivially computable: compute the transcript over the public challenges, derive each `z_i`, pick random `w_i`, set `R_i` and `s` as above, and serialize via `SchnorrAggregate::write`. No key knowledge, collusion, or timing is required.

### Recommendation
Bind the aggregated signature material into the weight transcript: in `SchnorrAggregate::verify` (and `SchnorrAggregator`), append each `R_i` and each public key (and message hash if applicable) to the digest before deriving weights — e.g. `digest.append_message(b"R", R_i.to_bytes())` and `append_message(b"key", A_i.to_bytes())` — so `z_i` commits to the exact values being verified. Optionally also fix `z_1 = 1` per the half-aggregation spec.

### Proof of Concept
1. Verifier calls `SchnorrAggregate::verify(dst, &[(A_1, c_1), …, (A_n, c_n)])` on an attacker-supplied aggregate.
2. Attacker reconstructs the digest: `DigestTranscript::<C::H>::new(dst)`, `domain_separate(b"signatures")`, `append_message(b"challenge", c_i)` for each `c_i`.
3. Attacker derives `z_1…z_n` via the same `weight()` calls, chooses random scalars `w_i`, sets `R_i = w_i·G − c_i·A_i`, `s = Σ z_i·w_i`.
4. `verify` computes `Σ z_i(w_i G − c_i A_i) + Σ z_i c_i A_i − (Σ z_i w_i) G = 0` → returns `true`, a forged aggregate over keys the attacker never controlled.

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L132-145)
```rust
    let mut digest = DigestTranscript::<C::H>::new(dst);
    digest.domain_separate(b"signatures");
    for (_, challenge) in keys_and_challenges {
      digest.append_message(b"challenge", challenge.to_repr());
    }

    let mut pairs = Vec::with_capacity((2 * keys_and_challenges.len()) + 1);
    for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
      let z = weight(&mut digest);
      pairs.push((z, self.Rs[i]));
      pairs.push((z * challenge, *key));
    }
    pairs.push((-self.s, C::generator()));
    multiexp_vartime(&pairs).is_identity().into()
```
