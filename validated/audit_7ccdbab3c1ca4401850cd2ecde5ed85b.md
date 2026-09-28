### Title
Aggregate Schnorr signature forgery: `weight()` commits only to challenges, letting an attacker choose `Rs`/`s` freely to satisfy the verification equation - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` derives the per-signature aggregation weight `z_i` from a transcript that commits **only to the Schnorr challenges**, never to `Rs`, the public keys, or `s`. Since `z_i` is therefore fully predictable to the attacker, anyone can fabricate an aggregate signature that verifies for an arbitrary public key/challenge pair — including challenges committing to messages and keys of victims who never signed — without knowing any discrete logarithm. This is the Serai-analog of "the guard checks only part of the input" (first-token allowlist): the Fiat-Shamir-style weight transcript binds only a subset of the attacker-controlled statement, so the remaining inputs can be solved for after the "random" coefficients are already fixed.

### Finding Description
`SchnorrAggregate::verify` builds the weight transcript as follows (crypto/schnorr/src/aggregate.rs:132-136):

```rust
let mut digest = DigestTranscript::<C::H>::new(dst);
digest.domain_separate(b"signatures");
for (_, challenge) in keys_and_challenges {
  digest.append_message(b"challenge", challenge.to_repr());
}
```

and then verifies `Σ z_i·R_i + Σ z_i·c_i·P_i − s·G == 0` (crypto/schnorr/src/aggregate.rs:138-145):

```rust
for (i, (key, challenge)) in keys_and_challenges.iter().enumerate() {
  let z = weight(&mut digest);
  pairs.push((z, self.Rs[i]));
  pairs.push((z * challenge, *key));
}
pairs.push((-self.s, C::generator()));
multiexp_vartime(&pairs).is_identity().into()
```

`Rs`, `s`, and the keys are supplied by the attacker via `SchnorrAggregate::read`/`write` (crypto/schnorr/src/aggregate.rs:77-104). The weights `z_i = weight(&mut digest)` (crypto/schnorr/src/aggregate.rs:22-65) are a deterministic public function of `dst` and the challenges `c_i` — all values the verifier-side caller discloses in `keys_and_challenges`. The security requirement of half-aggregation (eprint 2021/350, §3.3) is that `z_i` be unpredictable at the time the adversary commits to `R_i`; here the opposite holds — the adversary fixes `z_i` first, then solves for `R_i`/`s`.

Notably, `SchnorrAggregator` (crypto/schnorr/src/aggregate.rs:169-185) uses the identical transcript, so honestly produced aggregates still verify; the flaw is only on the adversarial-forgery side of `verify`.

### Impact Explanation
An unprivileged remote party who can submit an aggregate signature (serialized via `SchnorrAggregate::read`, an explicitly listed attacker-controlled input) can forge an aggregate that verifies for **any** `(public_key, challenge)` pair of their choosing — e.g., a challenge committing to a message under a victim validator's key for which no signature exists. That is a universal signature forgery against every public key, with zero signature knowledge. This is Critical-severity forgery of Schnorr signatures wherever `SchnorrAggregate::verify` gates acceptance of signed data.

### Likelihood Explanation
The attack requires no special position: the attacker needs only the target `(P, c)` pairs (public inputs to `verify`) and the freedom to submit `Rs`/`s`. The weights are deterministic functions of public data, so the forge succeeds with probability 1 on the first attempt — no grinding, no oracles, no honest co-signers needed. Wherever aggregate verification is on the acceptance path, exploitation is trivial.

### Recommendation
Commit the entire aggregate statement into the weight transcript before deriving `z_i`, i.e., append each `R_i`, each public key, `s` (or equivalently hash the full signature list) into `digest`, matching the half-aggregation construction where weights are unpredictable given the adversary's `R_i`. Concretely: in both `SchnorrAggregate::verify` and `SchnorrAggregator`, append `key.to_bytes()`, `R_i.to_bytes()` (and the signature count) to the transcript before each `weight()` call, keeping aggregator and verifier derivation sequences identical. The aggregator must then fix all `Rs` before `complete()` derives weights (which it already does, since `Rs` are taken from the stored signatures).

### Proof of Concept
Forgery of a single-signature aggregate for a victim key `P` and challenge `c` (e.g., `c = H(R_expected, P, msg)` for a `msg` the victim never signed):

```rust
// Attacker knows (P, c); both are public inputs to verify()
let z = {
  let mut digest = DigestTranscript::<C::H>::new(dst);
  digest.domain_separate(b"signatures");
  digest.append_message(b"challenge", c.to_repr());
  weight::<_, C::F>(&mut digest)
};

// Pick any s, solve for R to satisfy z*R + z*c*P - s*G == 0
let s = C::F::random(rng);
let R = (C::generator() * s - (P * (z * c))) * z.invert().unwrap();

let forged = SchnorrAggregate::<C> { Rs: vec![R], s };
// Serialized via SchnorrAggregate::write, delivered as untrusted bytes,
// parsed with SchnorrAggregate::read, then:
assert!(forged.verify(dst, &[(P, c)])); // verifies despite no signature from P
```

Generalizes to `n` claimed signatures: fix `s` and all but one `R_i` arbitrarily, solve the last `R_i` for the linear equation. No discrete logarithm of any `P_i` or `R_i` is ever needed.