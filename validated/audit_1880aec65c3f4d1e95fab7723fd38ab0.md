### Title
Schnorr half-aggregation weights omit the nonces and public keys, enabling universal signature forgery - (File: crypto/schnorr/src/aggregate.rs)

### Summary
The aggregate Schnorr verification in `SchnorrAggregate::verify` derives the per-signature weight `z_i` from a digest that commits only to the caller-supplied challenges. It never hashes the nonce commitments `Rs` nor the public keys being verified. This is a binding failure in the same class as the reported origin-confusion bug: the verifier accepts an object as authentic for a given key/message without the proof actually being bound to that context. Because the weights are computable purely from public inputs, an attacker can solve the verification equation algebraically and produce a "valid" `SchnorrAggregate` over arbitrary public keys and challenges without knowing any private key.

### Finding Description
`SchnorrAggregate::verify` at `crypto/schnorr/src/aggregate.rs:127-146` builds a `DigestTranscript` over the caller-supplied `dst`, domain-separates `b"signatures"`, and appends only `b"challenge"` messages: [1](#0-0) 

The multiexp relation checked is:

```
sum_i (z_i * R_i) + sum_i (z_i * c_i * A_i) - s * G == 0
```

where `z_i = weight(digest)` is a deterministic function of the challenge sequence only. Nothing in the transcript binds the points `R_i` or the keys `A_i`.

The referenced scheme (eprint 2021/350, half-aggregation) requires the coefficient for each signature to be a hash over the entire signature set — all nonces and keys — precisely so that no party can choose their nonce after learning the weights. Here, an attacker chooses the challenges (they are a public input computed from key/nonce/message by the attacker themselves), computes every `z_i` in advance, picks `R_1..R_{n-1}` and `s` arbitrarily, then solves for the final nonce:

```
R_n = ( s*G - sum_{i<n}(z_i*R_i + z_i*c_i*A_i) - z_n*c_n*A_n ) / z_n
```

Every term is public. The resulting `SchnorrAggregate { Rs, s }` serializes fine via `SchnorrAggregate::write`, parses via `SchnorrAggregate::read` (which only canonical-decodes points and a scalar, `crypto/schnorr/src/aggregate.rs:77-88`), and passes `verify` for keys the attacker does not control. Note that `C::read_G` here is the `Ciphersuite` point reader, not `Curve::read_G`, so even identity/non-rejecting decodings may be usable; regardless, arbitrary non-identity points suffice.

### Impact Explanation
Critical. `SchnorrAggregate::verify` is `#[must_use]` and is the sole authentication check for aggregated Schnorr signatures. Any consumer feeding attacker-controlled serialized aggregates and public keys/challenges into `SchnorrAggregate::read` + `verify` (the in-scope reachable path) will accept a fully forged aggregate attesting to signatures under arbitrary group/public keys. Within Serai this primitive is used for aggregated commit/consensus signature verification, so the forgery translates to acceptance of consensus messages that no signer ever produced — i.e., a forged signature accepted as authentic for an origin (key) that never signed, directly paralleling the reported same-origin bypass.

### Likelihood Explanation
High where reachable. The attack requires no private key material, no threshold collusion, and no interactive oracle — only the ability to submit `Rs`, `s`, and the keys/challenges list, all of which are attacker-controlled function inputs. `z_n` is nonzero with overwhelming probability (random-looking hash output), so the inversion always succeeds.

### Recommendation
Bind the weights to the full verification context: before deriving any weight, append every `R_i` (`self.Rs[i].to_bytes()`) and every public key `A_i` to the digest alongside its challenge — e.g., inside the loop over `keys_and_challenges`, `digest.append_message(b"nonce", self.Rs[i].to_bytes())` and `digest.append_message(b"key", key.to_bytes())`. Alternatively, adopt the exact half-aggregation challenge of eprint 2021/350 (a single hash over the ordered list of (key, message, nonce) tuples, with the first weight fixed to `C::F::ONE` so the aggregate remains non-malleable). Keep the `Rs.len() == keys_and_challenges.len()` length check.

### Proof of Concept
Conceptual (constants elided):

```rust
// Attacker goal: forge an aggregate "signed by" victim keys A_1..A_n
// under challenges c_i the attacker computes (c_i = hram(R_i, A_i, m_i)
// or whatever the caller uses — the attacker knows them all).

let n = keys_and_challenges.len();

// 1. Compute all weights offline: they depend only on the challenges.
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
for (_, c) in &keys_and_challenges {
    digest.append_message(b"challenge", c.to_repr());
}
let z: Vec<C::F> = (0..n).map(|_| weight(&mut digest)).collect();

// 2. Choose s and R_1..R_{n-1} freely.
let s = C::F::random(&mut rng);
let mut rs: Vec<C::G> = (0..n-1).map(|_| C::generator() * C::F::random(&mut rng)).collect();

// 3. Solve for R_n so the multiexp sums to identity:
//    z_n*R_n = s*G - sum_{i<n} z_i*R_i - sum_i z_i*c_i*A_i
let mut acc = C::generator() * s;
for i in 0..n-1 { acc -= rs[i] * z[i]; }
for (i, (a, c)) in keys_and_challenges.iter().enumerate() { acc -= *a * (z[i] * c); }
rs.push(acc * z[n-1].invert().unwrap());

let forged = SchnorrAggregate::<C> { Rs: rs, s };
assert!(forged.verify(dst, &keys_and_challenges)); // passes with no private key
```

The forgery works because `weight` never observes `self.Rs` or the keys (`aggregate.rs:132-143`), so the attacker can choose the final nonce after all weights are fixed.

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L127-146)
```rust
  pub fn verify(&self, dst: &'static [u8], keys_and_challenges: &[(C::G, C::F)]) -> bool {
    if self.Rs.len() != keys_and_challenges.len() {
      return false;
    }

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
  }
```
