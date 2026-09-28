### Title
Malformed FROST preprocess can force aggregate Taproot nonce to identity and panic BIP-340 signing - (crypto/frost/src/nonce.rs; networks/bitcoin/src/crypto.rs)

### Summary
The in-scope analog is malformed-but-canonical FROST preprocess commitments that make the aggregate nonce point `R` the identity for a Bitcoin Taproot signing session. The parser accepts the encodings, `BindingFactor::nonces` sums them into `Rs`, and the Bitcoin `Hram` then calls `x(R)`, which explicitly panics on the point at infinity. This is a reachable denial of service from untrusted `read_preprocess` bytes, matching the report’s class of accepted encoded input later surfacing as an unexpected internal exception.

### Finding Description
`TransactionSignMachine::read_preprocess` parses one FROST preprocess per Bitcoin input by delegating to each `AlgorithmSignMachine::read_preprocess`, which reads attacker-controlled nonce commitments through `Commitments::read`. Those commitments are only checked for canonical point encoding; there is no rejection of combinations whose per-generator sums `D = ΣD_l` or `E = ΣE_l` become identity. `BindingFactor::nonces` computes each aggregate nonce as `D + Σ rho_l·E_l`, so if the summed `D` and summed `E` are both identity, the resulting `R` is identity for every binding factor. `AlgorithmSignMachine::sign` passes `Rs` into `sign_share`, and Bitcoin’s BIP-340 `Hram::hram` hashes `x(R)` where `x` expects `encoded.x()` to exist and panics for infinity.

### Impact Explanation
A counterparty able to submit preprocess bytes for a signing round can deterministically crash the local signer instead of producing a signature share. For a Taproot key-spend this aborts transaction signing at `taproot_key_spend_signature_hash`/share generation time, denying availability of the signing path. The triggering objects are ordinary canonical curve points, so this does not depend on DER, truncation, non-canonical encodings, leaked secrets, collusion beyond the supplied preprocess, or malicious validator infrastructure.

### Likelihood Explanation
For a two-party signing set over the single-generator Schnorr nonce used by Bitcoin `Schnorr::nonces`, the attacker copies the victim’s public preprocess commitments `(D_v, E_v)` and submits `(−D_v, −E_v)` as their own. Both points remain valid canonical encodings, while `D_v + (−D_v) = identity` and `E_v + (−E_v) = identity`; `BindingFactor::nonces` then yields `R = identity + rho·identity = identity` regardless of `rho`. When `sign_share` runs, `Hram::hram` calls `x(R)` and reaches `expect("point at infinity")`. The attack requires supplying a preprocess for an included participant and having the aggregate nonce sums cancel; it does not require controlling a threshold by itself for the panic to affect an honest signer’s session.

### Recommendation
Reject aggregate nonce identities before calling algorithm `sign_share`/`verify`, and return `FrostError` rather than allowing point extraction to panic. Concretely, after `let Rs = B.nonces(&nonces);` in `crypto/frost/src/sign.rs`, validate every required nonce point is non-identity before use, or make Bitcoin `x`/`Hram` return a signature/algorithm error instead of `expect`. Keep canonical `read_G` checks, but add semantic aggregate checks in FROST rather than relying on per-point encoding validity.

### Proof of Concept
For one-input Bitcoin `TransactionMachine`, `Schnorr::nonces` is `vec![vec![generator]]`, so each preprocess contains one nonce commitment pair `(D, E)`. Read the victim preprocess `(D_v, E_v)`, then provide as the other included participant a preprocess whose same nonce/generator entry encodes `(D_a, E_a) = (-D_v, -E_v)`. `read_preprocess` accepts both because each point is canonical; `B.nonces` computes `D_v + D_a = identity` and `E_v + E_a = identity`, producing `Rs[0][0] = identity`. `AlgorithmSignMachine::sign` then calls `sign_share`, Bitcoin `Hram::hram` inputs `x(R)`, and `x` panics on `encoded.x().expect("point at infinity")` before a share is produced. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L351-353)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
  }
```

**File:** crypto/frost/src/sign.rs (L382-399)
```rust
    #[allow(non_snake_case)]
    let Rs = B.nonces(&nonces);

    let our_binding_factors = B.binding_factors(multisig_params.i());
    let nonces = self
      .nonces
      .drain(..)
      .enumerate()
      .map(|(n, nonces)| {
        let [base, mut actual] = nonces.0;
        *actual *= our_binding_factors[n];
        *actual += base.deref();
        actual
      })
      .collect::<Vec<_>>();

    let share = self.params.algorithm.sign_share(&view, &Rs, nonces, msg);

```

**File:** crypto/frost/src/nonce.rs (L194-211)
```rust
  pub(crate) fn nonces(&self, planned_nonces: &[Vec<C::G>]) -> Vec<Vec<C::G>> {
    let mut nonces = Vec::with_capacity(planned_nonces.len());
    for n in 0 .. planned_nonces.len() {
      nonces.push(Vec::with_capacity(planned_nonces[n].len()));
      for g in 0 .. planned_nonces[n].len() {
        #[allow(non_snake_case)]
        let mut D = C::G::identity();
        let mut statements = Vec::with_capacity(self.0.len());
        #[allow(non_snake_case)]
        for IndividualBinding { commitments, binding_factors } in self.0.values() {
          D += commitments.nonces[n].generators[g].0[0];
          statements
            .push((binding_factors.as_ref().unwrap()[n], commitments.nonces[n].generators[g].0[1]));
        }
        nonces[n].push(D + multiexp_vartime(&statements));
      }
    }
    nonces
```

**File:** networks/bitcoin/src/crypto.rs (L12-22)
```rust
/// Panics on invalid input.
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}

/// Convert a non-infinity point to a XOnlyPublicKey (dropping its sign).
///
/// Panics on invalid input.
pub(crate) fn x_only(key: &ProjectivePoint) -> XOnlyPublicKey {
  XOnlyPublicKey::from_slice(&x(key)).expect("x_only was passed a point which was infinity or odd")
```

**File:** networks/bitcoin/src/crypto.rs (L59-66)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
```

**File:** crypto/ciphersuite/src/lib.rs (L91-100)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let mut encoding = <Self::G as GroupEncoding>::Repr::default();
    reader.read_exact(encoding.as_mut())?;

    let point = Option::<Self::G>::from(Self::G::from_bytes(&encoding))
      .ok_or_else(|| io::Error::other("invalid point"))?;
    if point.to_bytes().as_ref() != encoding.as_ref() {
      Err(io::Error::other("non-canonical point"))?;
    }
    Ok(point)
```

**File:** crypto/frost/src/algorithm.rs (L182-190)
```rust
  fn nonces(&self) -> Vec<Vec<C::G>> {
    vec![vec![C::generator()]]
  }

  fn preprocess_addendum<R: RngCore + CryptoRng>(&mut self, _: &mut R, _: &ThresholdKeys<C>) {}

  fn read_addendum<R: Read>(&self, _: &mut R) -> io::Result<Self::Addendum> {
    Ok(())
  }
```
