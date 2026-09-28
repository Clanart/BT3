### Title
Aggregate Schnorr signature verification accepts universally forged signatures - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` computes randomized aggregation weights only from the caller-supplied challenges, then verifies a linear relation over attacker-supplied `Rs`, public keys, challenges, and `s`. Because `R_i` is not bound to the challenge-generation transcript and is otherwise unconstrained, an attacker can choose `R_i = -c_i A_i` and `s = 0`, making every weighted term cancel without knowing any private key or Schnorr nonce. [1](#0-0) 

### Finding Description
The verifier creates a deterministic transcript from `dst`, appends each supplied challenge, derives a weight `z_i` for each position, and checks:

```text
sum_i(z_i * R_i + z_i * c_i * A_i) - s * G = identity
```

This is implemented in `crypto/schnorr/src/aggregate.rs`. [1](#0-0) 

Although `SchnorrAggregate::read` enforces canonical point and scalar encodings, it does not establish that each `R_i` was produced as a genuine Schnorr nonce commitment. [2](#0-1) 

The aggregation weights commit only to the sequence of challenges and omit both `R_i` and the public key `A_i`. [3](#0-2) 

Consequently, after reproducing the public weights, an attacker can algebraically cancel the public-key term for every claimed signature. The verifier accepts because the grouped equality reaches identity. [4](#0-3) 

### Impact Explanation
Any application that accepts `SchnorrAggregate` as proof that multiple Schnorr signatures are valid can accept an aggregate for keys whose owners never signed and for challenges the attacker cannot satisfy individually. This is a complete signature-forgery primitive within the aggregate verification API, not merely signature malleability. [5](#0-4) 

The attack requires only public inputs: the aggregate bytes supplied to `SchnorrAggregate::read`, the public keys, the public challenges, and the verifier’s `dst`. [2](#0-1) [6](#0-5) 

### Likelihood Explanation
The construction is deterministic and computationally trivial: the attacker derives each public `z_i`, computes `R_i = -c_i * A_i`, and sets `s = 0`. No secret key, valid individual signature, nonce reuse, malformed encoding, or protocol collusion is required. [1](#0-0) 

The only precondition is that the caller exposes `SchnorrAggregate::verify` for attacker-controlled aggregate data under a known `dst`, which is precisely the API’s intended use for deserialized aggregate signatures. [2](#0-1) [7](#0-6) 

### Recommendation
Do not allow the verifier to accept arbitrary `R_i` values whose only constraint is cancellation against `c_i * A_i`. At minimum, bind each `R_i`, public key, challenge, index, and statement domain into the aggregation-weight transcript so replacement or cancellation components alter the weights; this alone does not fully solve the forgery because the algebraic term remains attacker-controlled, so the construction should additionally require each `R_i` to be bound to an externally established, genuinely signed statement or redesign the aggregate format and security model. [1](#0-0) 

Verification should also reject degenerate aggregates and ensure the aggregate length corresponds to a validated set of signed statements rather than treating `(Rs, s)` as sufficient evidence on its own. [2](#0-1) [8](#0-7) 

### Proof of Concept
For each public key `A_i` and challenge `c_i`, reproduce the verifier’s transcript:

```text
T = DigestTranscript::<C::H>::new(dst)
T.domain_separate("signatures")
for each i:
    T.append_message("challenge", c_i)
    z_i = weight(T)
```

Then construct:

```text
R_i = -c_i * A_i
s   = 0
```

The verifier computes:

```text
sum_i(z_i * R_i + z_i * c_i * A_i) - s * G
= sum_i(z_i * (-c_i * A_i) + z_i * c_i * A_i) - 0 * G
= identity
```

Thus `SchnorrAggregate::verify` returns true without the attacker knowing any discrete logarithm or possessing any individual signature. [1](#0-0) 

A serialized forged aggregate only needs canonical point encodings for the computed `R_i` values followed by the canonical encoding of scalar zero, both of which are accepted by the deserializer. [2](#0-1)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L77-87)
```rust
  pub fn read<R: Read>(reader: &mut R) -> io::Result<Self> {
    let mut len = [0; 4];
    reader.read_exact(&mut len)?;

    #[allow(non_snake_case)]
    let mut Rs = vec![];
    for _ in 0 .. u32::from_le_bytes(len) {
      Rs.push(C::read_G(reader)?);
    }

    Ok(SchnorrAggregate { Rs, s: C::read_F(reader)? })
```

**File:** crypto/schnorr/src/aggregate.rs (L127-145)
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
```
