### Title
Schnorr aggregate verification weights do not bind the nonce points/keys, enabling forged aggregate signatures - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` computes per-signature weights `z_i` from a transcript that commits **only to the challenges**, not to the public keys or the `R` points being verified. Because the verifier's random coefficients are fully predictable before the attacker chooses `Rs` (and the attacker controls `keys_and_challenges`), an attacker can construct an aggregate that sums to identity while no individual component is a valid Schnorr signature — including entries under victim public keys who never signed anything.

### Finding Description
In `verify`, the transcript absorbs each `challenge` and then squeezes the weights sequentially: [1](#0-0) 

The check is `Σ z_i·(R_i + c_i·K_i) − s·G = 0`. The weight `z_i` is a deterministic function of the challenge list only (`weight()` draws from `digest.challenge(b"aggregation_weight")` after all challenges are absorbed, `aggregate.rs:22-65`, `134-136`). It never commits to `R_i` or `K_i`.

This mirrors the BLST aggregation disclosure class (CL-2021-20): aggregation that combines attacker-controlled points without binding each term to its inputs lets terms cancel. Here, the attacker knows every `z_i` before committing to the `Rs`, so they can make each term equal an arbitrary value: choosing `R_i = T_i − c_i·K_i` makes term `i` equal `z_i·T_i`, and the `T_i` can be chosen to cancel (`T_1 = −(z_0/z_1)·T_0`), leaving `s = 0`.

Reachability: `SchnorrAggregate::read` (`aggregate.rs:77-88`) reads `Rs` via `C::read_G` and `s` via `C::read_F` from untrusted bytes, and `verify` is a public API taking attacker-supplied `keys_and_challenges`. This is the intended threat model the code itself documents ("a malicious adversary" forging signatures, `aggregate.rs:120-125`).

### Impact Explanation
Any consumer relying on a valid `SchnorrAggregate` as proof that each `(key, challenge)` pair corresponds to a genuinely-issued Schnorr signature can be defrauded: the attacker produces an aggregate claiming a victim key signed an arbitrary challenge, which verifies. Since `s` is shared, component-level forgery is invisible. Downstream blame/accounting keyed to individual `R`s/keys is equally forgeable.

### Likelihood Explanation
The attacker needs only to control the `Rs` bytes and the `(key, challenge)` list presented to `verify` — all public inputs. No secret, no honest-participant misbehavior, no probabilistic precondition. Exploitation is deterministic and computationally trivial (scalar arithmetic).

### Recommendation
Bind every component to its weight: absorb `key.to_bytes()` and `R.to_bytes()` into the digest for that index before deriving `z_i` (i.e., transcript `(key_i, R_i, challenge_i)` per entry, as in standard eprint 2021/350-style aggregation), so a weight cannot be computed before the points it multiplies are fixed.

### Proof of Concept
For curve `C`, victim key `K0`, attacker key `K1`, arbitrary challenges `c0, c1`:

1. Build the digest: `DigestTranscript::<C::H>::new(dst)`, `domain_separate(b"signatures")`, append `c0`, `c1`.
2. Extract `z0 = weight(&mut digest)`, `z1 = weight(&mut digest)` — fully computable offline.
3. Pick any `T0` (e.g., `C::generator()`), set `T1 = −(z0/z1)·T0`.
4. Set `R0 = T0 − c0·K0`, `R1 = T1 − c1·K1`, `s = 0`.
5. Serialize `SchnorrAggregate { Rs: [R0, R1], s: 0 }` and call `verify(dst, &[(K0, c0), (K1, c1)])`.

The multiexp computes `z0·T0 + z1·T1 − 0·G = z0·T0 − z0·T0 = identity`, so `verify` returns `true`, yet `K0` never produced a signature for `c0`.

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
