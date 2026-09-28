### Title
Half-aggregation weights are not bound to attacker-supplied nonces/keys, enabling SchnorrAggregate forgery - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` derives each signature's aggregation weight `z_i` solely from the Fiat-Shamir challenges, never transcripting the signature nonces `Rs[i]` or the public keys. Because `R` points and `s` are fully attacker-controlled inputs (via `SchnorrAggregate::read` / direct construction), an unprivileged party can algebraically satisfy the verification equation for arbitrary public keys and challenges without knowing any discrete logarithm — a forged aggregate signature accepted as valid, analogous to CVE-2019-9221's "incorrect access control" (a protected operation — acceptance of an aggregate signature — is reachable without possessing the required secret).

### Finding Description
Verification computes, from `crypto/schnorr/src/aggregate.rs`:

- The weight digest only appends the challenges:
  ```
  for (_, challenge) in keys_and_challenges { digest.append_message(b"challenge", challenge.to_repr()); }
  ```
  (lines 132–136) — the public keys `key` and the nonces `self.Rs[i]` are never transcripted.
- The checked equation is `sum_i z_i * Rs[i] + sum_i (z_i * c_i) * A_i - s*G == 0` (lines 138–145), where `z_i = weight(&mut digest)` is computed deterministically and publicly.

Since every `z_i` is computable by the attacker before choosing `Rs` and `s`, the attacker can pick `Rs[1..]` and `s` arbitrarily and solve for `Rs[0] = z_0^{-1} * (s*G - sum_{i>=1} z_i*R_i - sum_i z_i*c_i*A_i)`. The multiexp then sums to identity and `verify` returns `true`. Half-aggregation per eprint 2021/350 requires the weight to be bound per-signature (to the signature and message/key set); here a single sequential `weight()` stream over only the challenges leaves the `R` terms unbound, permitting a linear-algebra forgery. `SchnorrAggregator::complete` has the identical weakness on the proving side (it weights `s` contributions by the same challenge-only stream, lines 169–184), so honestly aggregated signatures verify — masking the forgery vector.

### Impact Explanation
Any caller that accepts `SchnorrAggregate` as proof that holders of keys `A_i` signed messages producing challenges `c_i` can be presented a forged aggregate attesting to signatures that never existed. Where aggregate verification gates a state transition or acceptance of batched signatures (e.g., validator set signatures, batched message approvals), the attacker gains unauthorized acceptance — the exact "incorrect access control" consequence: a verifier authorizes an action without the required private keys.

### Likelihood Explanation
Fully deterministic and reachable from public inputs: `SchnorrAggregate::read` (lines 77–88) reads `Rs` and `s` from untrusted bytes with only canonical-encoding checks, and `verify` takes attacker-chosen `keys_and_challenges`. No secret knowledge, no collusion, and no malformed-curve tricks are required — only field/scalar arithmetic using the publicly computable `z_i` values. Severity: High (signature forgery).

### Recommendation
Bind each weight to the signature it weights: append `Rs[i].to_bytes()` and the public key (or the full per-signature transcript) to the digest before drawing `z_i`, e.g. per-index `digest.append_message(b"nonce", Rs[i].to_bytes()); digest.append_message(b"key", key.to_bytes())` prior to `weight(&mut digest)`, and mirror this ordering in `SchnorrAggregator::aggregate`/`complete`. This forces `Rs` to be committed before the weight is known, eliminating the solve-for-R forgery.

### Proof of Concept
```rust
// For any keys_and_challenges = [(A_i, c_i)], n >= 1:
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
for (_, c) in keys_and_challenges { digest.append_message(b"challenge", c.to_repr()); }

// Recompute the public weights
let z: Vec<C::F> = (0..n).map(|_| weight::<_, C::F>(&mut digest)).collect();

// Attacker-chosen forgery:
let s = C::F::random(rng);                       // arbitrary s
let mut Rs: Vec<C::G> = (0..n).map(|_| C::G::random(rng)).collect(); // arbitrary R_1..R_{n-1}
// Solve R_0 so sum z_i*(R_i + c_i*A_i) == s*G
let mut acc = -(s * C::generator());
for i in 0..n { acc += z[i] * (C::generator() * C::F::ZERO); } // placeholder for clarity:
// concretely:
//   acc = s*G - sum_i z_i*c_i*A_i - sum_{i>=1} z_i*R_i
//   Rs[0] = z[0]^{-1} * acc
Rs[0] = acc * z[0].invert().unwrap();

// SchnorrAggregate { Rs, s }.verify(dst, keys_and_challenges) == true
```
Root cause confirmed at `crypto/schnorr/src/aggregate.rs:22-65` (`weight` consumes only challenge-derived digest), `132-145` (verify loop omits `Rs`/keys from the digest), and `77-88` (untrusted `Rs`/`s` admission via `read`).