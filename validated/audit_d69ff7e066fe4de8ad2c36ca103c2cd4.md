### Title
Empty aggregate Schnorr signatures verify successfully - (File: crypto/schnorr/src/aggregate.rs)

### Summary
`SchnorrAggregate::verify` accepts an aggregate containing zero nonces when paired with an empty `keys_and_challenges` list. A serialized aggregate consisting of a zero signature count and scalar `s = 0` therefore verifies without any signer or private key. [1](#0-0) 

### Finding Description
`SchnorrAggregate::read` accepts an encoded length of zero and then reads only the scalar `s`; canonical zero is a valid scalar. [2](#0-1)  During verification, the implementation only requires `self.Rs.len() == keys_and_challenges.len()`, so two empty lists satisfy the check. [3](#0-2)  The verification loop adds no nonce or public-key terms for an empty aggregate, and the sole remaining term is `-s * G`; with `s = 0`, this term is the identity and verification succeeds. [4](#0-3) 

### Impact Explanation
An unprivileged party can forge a serialized aggregate signature accepted by `verify` for an empty signer set. This is an incorrect verifier formula: successful verification no longer implies that at least one listed signer produced a valid Schnorr signature. If a caller permits an empty claimed-signer list, the forged value can be interpreted as a valid aggregate signature despite having no signatures and no corresponding private keys. [1](#0-0) 

### Likelihood Explanation
The attack requires only attacker-controlled bytes passed to `SchnorrAggregate::read` and an empty public `keys_and_challenges` slice passed to `verify`. No malformed point encodings, secret material, signer participation, or protocol privileges are needed. [2](#0-1)  Exploitation depends on a downstream verifier accepting or supplying an empty signer list, so the practical severity is Medium rather than higher. [1](#0-0) 

### Recommendation
Reject empty aggregates in `SchnorrAggregate::verify`. The verifier should return `false` when `keys_and_challenges.is_empty()` or `self.Rs.is_empty()`, independently of the shared length check. Adding a regression test for an encoded empty aggregate with `s = 0` would prevent reintroduction. [1](#0-0) 

### Proof of Concept
For a ciphersuite whose scalar encoding accepts 32 zero bytes, construct the wire encoding as a little-endian nonce count of zero followed by `s = 0`:

```rust
let mut encoded = Vec::new();
encoded.extend_from_slice(&0u32.to_le_bytes()); // Rs.len() == 0
encoded.extend_from_slice(&[0u8; 32]);          // s == 0 for common 256-bit suites

let aggregate =
  SchnorrAggregate::<C>::read(&mut encoded.as_slice()).unwrap();

assert!(aggregate.verify(dst, &[]));
```

`read` succeeds because there are no point encodings to reject and the zero scalar is canonical. `verify` then creates no statement pairs except the identity-producing `-0 * G` term and returns `true`. [2](#0-1) [4](#0-3)

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
