### Title
`SchnorrAggregate::verify` accepts a forged empty aggregate signature (zero `Rs`, `s = 0`) - (File: crypto/schnorr/src/aggregate.rs)

### Summary
The TrustedVolumes incident class is "untrusted bytes reach a verification/settlement path that accepts an unintended object." The reachable analog in Serai's in-scope crypto is `SchnorrAggregate::read` followed by `SchnorrAggregate::verify`: a byte stream encoding an aggregate with zero nonces and `s = 0` verifies as a valid aggregate signature over an empty signer set, with no rejection of the vacuous case.

### Finding Description
`SchnorrAggregate::read` parses a `u32` length, then reads that many `R` points and one scalar `s`, with no lower bound on the length (crypto/schnorr/src/aggregate.rs:77-88). `SchnorrAggregate::verify` then:

```rust
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

When `keys_and_challenges` is empty and `self.Rs` is empty, the only pair is `(-s, G)`. If the attacker supplies `s = 0`, `multiexp_vartime` returns the identity and verification returns `true`. The empty-aggregate case is never rejected — `SchnorrAggregator::complete` refuses to produce an empty aggregate (`if self.sigs.is_empty() { return None }`), proving the empty case is meaningless on the honest path, yet the verifier accepts a byte-serialized forgery of exactly that shape.

### Impact Explanation
Any context that feeds untrusted bytes to `SchnorrAggregate::read` and then calls `verify` can be handed a trivially forgeable "valid" aggregate signature attesting no signers. In the Tendermint usage (`coordinator/tributary/src/tendermint/mod.rs:200-229`), `verify_aggregate` only checks `signers.len() == aggregate.Rs().len()` before calling `aggregate.verify`; a consensus payload claiming an aggregate over an empty signer list, with `sig = len(0) || s(0)`, would verify. Whether a caller accepts an empty signer set is the gating question, but the crypto primitive itself returns `true` for a signature that binds to no key, no nonce, and no message — an incorrect verifier formula for the vacuous case.

### Likelihood Explanation
The forgery is deterministic and requires no key material, only the encoding `0x00000000 || 0x00…00`. Exploitability depends on whether any production caller can reach `verify` with an empty `keys_and_challenges` while treating a `true` result as authorization — that is a caller-side policy question outside the crypto crate. In aggregate-signature verifiers generally, an empty-set accept is a recognized footgun (BLS implementations explicitly special-case it); here the honest constructor refuses to emit the value the verifier accepts.

### Recommendation
In `SchnorrAggregate::verify`, return `false` early when `self.Rs.is_empty()` (mirroring `SchnorrAggregator::complete`'s `None` for the empty case). Optionally also reject `s == 0` and identity `Rs` in `read`/`verify` for defense in depth.

### Proof of Concept
```rust
// For any C: Ciphersuite (e.g. Ristretto)
let forged_bytes: Vec<u8> = {
  let mut v = 0u32.to_le_bytes().to_vec();          // zero Rs
  v.extend_from_slice(<C::F as PrimeField>::ZERO.to_repr().as_ref()); // s = 0
  v
};
let agg = SchnorrAggregate::<C>::read::<&[u8]>(&mut forged_bytes.as_ref()).unwrap();
assert!(agg.verify(b"any dst", &[])); // empty signer set verifies
```
This reaches `verify` purely through attacker-controlled bytes fed to `SchnorrAggregate::read`, satisfying the unprivileged-party reachability rule.

Note on scope confidence: this is a concrete, provable incorrect-verifier-formula analog; its practical severity is bounded by whether an in-production caller treats an empty signer set as meaningful (the Tendermint `SignatureScheme` wrapper checks length equality but not emptiness, and consensus-level weight thresholds are the mitigating layer). No stronger analog (key-share recovery, unintended-message signing) was conclusively established in the inspected code paths — FROST signing-set validation (`sign.rs:290-313`), PedPoP PoK binding (`pedpop/src/lib.rs:86-94`), `ThresholdKeys::new`/`view` participant and interpolation checks, and the BIP-340 `Hram`/`verify` parity handling in `networks/bitcoin/src/crypto.rs` were all reviewed and appear internally consistent.