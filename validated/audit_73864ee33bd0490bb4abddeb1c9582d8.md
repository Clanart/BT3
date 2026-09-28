### Title
Forged aggregate Schnorr signatures via challenge-only aggregation weights - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` derives each aggregate weight solely from the ordered signature challenges. The signer-controlled nonce points and public keys are not committed to the weight transcript, so an attacker can craft multiple invalid component statements which cancel in the final multiexponentiation. This permits a forged aggregate signature which verifies as though it represented valid Schnorr signatures.

### Finding Description
`SchnorrAggregate::read` accepts an arbitrary attacker-controlled list of nonce points and scalar `s`. During verification, the digest is initialized with the domain separator and each challenge, then `weight` is called for every `(public_key, challenge)` pair. Neither the nonce point nor public key is appended to the digest before deriving the corresponding weight. The verifier then checks `sum(z_i * R_i + z_i * c_i * A_i) - sG == identity` in variable time. [1](#0-0) [2](#0-1) 

Because all `z_i` values are publicly computable before choosing the `R_i` values, an attacker can make two invalid individual Schnorr equations cancel. For a target statement `(A1, c1)` and a second statement `(A2, c2)`, choose any nonzero point `X`, compute the deterministic weights `z1` and `z2`, and set:

```text
R1 = -(c1 * A1) + inverse(z1) * X
R2 = -(c2 * A2) - inverse(z2) * X
s  = 0
```

The aggregate check becomes:

```text
z1 * (-c1A1 + z1^-1 X + c1A1)
+ z2 * (-c2A2 - z2^-1 X + c2A2)
- 0G
= X - X
= identity
```

Both component signatures are invalid whenever `X` is not identity, yet the aggregate verifies.

### Impact Explanation
An attacker can produce an aggregate signature accepted for a public-key/message pair without knowledge of the corresponding private key. Any verifier treating successful `verify` as proof that every listed Schnorr signature is valid can accept forged authorization. This is a signature forgery, not merely a malformed-input rejection failure.

### Likelihood Explanation
The attacker only needs two `(public_key, challenge)` entries accepted by the verifier and can supply `R1`, `R2`, and `s = 0` through the public serialization interface. The attack is deterministic: the weights are computable solely from the supplied challenges because `digest` commits to challenges but omits `R` and the public keys. [3](#0-2) 

### Recommendation
Bind every aggregation weight to the complete statement being weighted, including the signature nonce, public key, and challenge—or otherwise use unpredictable verifier-side weights. For example, append each `(R_i, A_i, c_i)` tuple to the digest before deriving `z_i`, then have the prover use the same complete transcript when summing weighted `s` values. The aggregate format would need to carry enough data to reproduce those weights securely.

### Proof of Concept
```text
Given two verification entries:
  (A1, c1), (A2, c2)

1. Reconstruct the verifier transcript:
   DigestTranscript::<C::H>::new(dst)
   domain_separate("signatures")
   append_message("challenge", c1)
   append_message("challenge", c2)

2. Compute:
   z1 = weight(...)
   z2 = weight(...)

3. Choose:
   X  = G
   R1 = -(c1 * A1) + inverse(z1) * X
   R2 = -(c2 * A2) - inverse(z2) * X
   s  = 0

4. Submit SchnorrAggregate { Rs: [R1, R2], s }.

5. Verification computes:
   z1 * (R1 + c1A1) + z2 * (R2 + c2A2)
   = z1 * (z1^-1 X) + z2 * (-z2^-1 X)
   = X - X
   = identity
```

`verify` returns `true` even though neither `R1 + c1A1 - 0G` nor `R2 + c2A2 - 0G` is identity. [4](#0-3)

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L78-87)
```rust
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
