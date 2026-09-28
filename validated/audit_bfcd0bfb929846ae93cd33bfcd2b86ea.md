### Title
`SchnorrAggregate::verify` accepts an aggregate over zero signers (`s = 0`, `Rs = []`) as valid - (File: crypto/schnorr/src/aggregate.rs)

### Summary
Analogous to Solmate's `SafeTransferLib` returning `true` when calling a token contract that has no code, `SchnorrAggregate::verify` returns `true` when verifying an aggregate signature over a signer set that does not exist. An aggregate containing zero `Rs` and `s = 0` satisfies the verification equation vacuously, because the multiexp reduces to `-0 * G = identity` and there is no check that at least one signer/signature was actually aggregated.

### Finding Description
`SchnorrAggregate::read` accepts an empty `Rs` vector followed by a scalar `s`, with no minimum-length constraint: [1](#0-0) 

`verify` then only checks `self.Rs.len() != keys_and_challenges.len()` and builds the pair list: [2](#0-1) 

When both are empty, `pairs` consists solely of `(-self.s, C::generator())`. With `s = 0`, `multiexp_vartime` returns the identity point and `verify` returns `true`. The byte encoding `len = 0u32` + a zero scalar is a fully canonical, attacker-craftable input that produces a "valid" aggregate signature attesting to nothing — precisely the "operation on a non-existent entity returns success" shape of the reference bug. A live caller of this pattern exists in `Validators::verify_aggregate` (`coordinator/tributary/src/tendermint/mod.rs:201-228`), which only requires `signers.len() == aggregate.Rs().len()`; an empty `signers` list paired with a `0 || 0-scalar` aggregate signature verifies as a valid aggregate vote.

### Impact Explanation
Any downstream logic that treats `verify(...) == true` as evidence that some set of validators/participants signed a message can be induced to accept a "signature" backed by no key and no nonce. Wherever the signer set is derived from a filter that may legitimately be empty (e.g., validators matching a condition, participants who submitted preprocesses), an attacker can forge the aggregate signature bytes in constant time — no discrete logs, no hash grinding — and have verification succeed. This is an incorrect verifier formula for a degenerate case: the empty conjunction is satisfied by the zero signature.

### Likelihood Explanation
Reachability requires a caller that can be driven to invoke `verify` with an empty `keys_and_challenges`/signer list while accepting attacker-supplied aggregate bytes. `SchnorrAggregate::read` is among the untrusted-bytes entry points, and `verify_aggregate` paths that zip `signers` with `Rs` make the empty case reachable whenever an empty subset is plausible. The cryptographic difficulty is zero once reached. Severity is bounded by the fact that callers must have a code path where the signer set can be empty for the bypass to be meaningful, hence Medium rather than High.

### Recommendation
In `SchnorrAggregate::verify` (and/or `SchnorrAggregate::read`), reject the degenerate empty aggregate: return `false`/`Err` when `self.Rs.is_empty()` or `keys_and_challenges.is_empty()`, analogous to checking `account.code.length > 0` before trusting a low-level call result. Alternatively require `Rs.len() >= 1` at deserialization. This mirrors the hardening already applied elsewhere, e.g., `Curve::read_G` rejecting identity points (`crypto/frost/src/curve/mod.rs:125-131`) and `Signed::read` rejecting identity nonces (`coordinator/tributary/src/transaction.rs:62-69`).

### Proof of Concept
```rust
// crypto/schnorr SchnorrAggregate over any Ciphersuite C
// Attacker-controlled bytes: 4-byte count = 0, then s = 0
let mut bytes = vec![];
bytes.extend(0u32.to_le_bytes());              // zero Rs
bytes.extend(C::F::ZERO.to_repr().as_ref());   // s = 0

let agg = SchnorrAggregate::<C>::read(&mut bytes.as_slice()).unwrap();
// Verifies successfully despite no signer, key, or nonce existing
assert!(agg.verify(b"any-dst", &[]));
```
The multiexp in `verify` reduces to `-0 * G`, which is the identity, so `is_identity()` returns true. In `Validators::verify_aggregate` (`coordinator/tributary/src/tendermint/mod.rs:211-228`), `signers = []` satisfies the length check and produces an empty `keys_and_challenges`, making the forged aggregate verify.

### Citations

**File:** crypto/schnorr/src/aggregate.rs (L77-88)
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
  }
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
