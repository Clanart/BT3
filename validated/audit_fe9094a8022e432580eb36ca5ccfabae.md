### Title
Malicious identity-producing FROST preprocess crashes Bitcoin Schnorr signing - (File: `networks/bitcoin/src/crypto.rs`)

### Summary
An untrusted signing participant can craft a FROST preprocess whose nonce commitments cancel the other participants’ nonce commitments, causing the aggregate nonce `R` to be the point at infinity. `bitcoin-serai`’s BIP-340 `Hram::hram` then calls `x(R)`, which unconditionally unwraps `encoded.x()` and panics for infinity. This is a reachable Rust analog of member access on an invalid/null object: public preprocess bytes induce an unchecked special-case object state followed by a fatal dereference.

### Finding Description
`read_preprocess` accepts attacker-controlled commitments through `Commitments::read`, and each commitment point is decoded using `C::read_G` without rejecting the group identity. During `sign`, the aggregate nonce is formed as the sum of every participant’s `D` plus the binding-factor-weighted sum of every participant’s `E`. A malicious participant can set `D_malicious = -sum(D_honest)` and `E_malicious = identity`, making the resulting `R` the point at infinity for any binding factor. FROST then invokes the Bitcoin Schnorr algorithm’s `sign_share`, which calls `Hram::hram(&nonce_sums[0][0], ...)`. `Hram::hram` passes `R` to `x`, and `x` panics on infinity because `encoded.x()` is absent. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) 

### Impact Explanation
A malicious threshold participant can reliably abort signing after preprocesses are exchanged by forcing `R` to infinity. The panic occurs before the victim emits a signature share, so the attacker can repeatedly prevent transaction signing without needing key material, validator privileges, malformed local keys, or unsafe code. This is a concrete availability failure reachable through public signing-session messages. [6](#0-5) [7](#0-6) 

### Likelihood Explanation
The attack only requires participation in a signing session and the ability to submit a preprocess after observing the other commitments. The malicious commitment values are valid canonical group encodings, including the identity, so they pass `read_preprocess`. Unlike random identity nonces, cancellation does not depend on probability because the attacker directly chooses `-sum(D_honest)`. [8](#0-7) [9](#0-8) 

### Recommendation
Reject identity nonce commitments and any aggregate nonce that is the identity before calling `sign_share`/`Hram::hram`. At minimum, `AlgorithmSignMachine::sign` should check every `Rs[n][g]` for `is_identity()` and return `FrostError` rather than panicking. For the Bitcoin algorithm, `Hram::hram` should also return an error-compatible failure path or `sign_share` should prevalidate `R` and the group key, since BIP-340 cannot encode the point at infinity as an x-only public key. [10](#0-9) [11](#0-10) 

### Proof of Concept
For the Bitcoin `Schnorr` algorithm, each participant preprocess contains one `(D, E)` pair because `nonces()` returns `vec![vec![generator()]]`. Let the honest participants’ decoded commitments produce aggregate first components `D_sum` and second components `E_sum`. An attacker submits:

```text
D_attacker = -D_sum
E_attacker = C::G::identity().to_bytes()
```

Then `BindingFactor::nonces` computes:

```text
R = (D_sum + D_attacker) + rho * (E_sum + identity)
  = identity
```

`Schnorr::sign_share` calls `Hram::hram(R = identity, A = group_key, msg)`, which calls `x(R)`. Since `identity.to_encoded_point(true).x()` is `None`, `expect("point at infinity")` panics. A regression test can construct this without network code by creating honest preprocesses, computing their summed `D` commitments, feeding the negation plus the canonical identity encoding to `read_preprocess`, and invoking `AlgorithmSignMachine::sign` with the resulting `HashMap`. [12](#0-11) [13](#0-12) [14](#0-13)

### Citations

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

**File:** crypto/frost/src/nonce.rs (L34-36)
```rust
  fn read<R: Read>(reader: &mut R) -> io::Result<GeneratorCommitments<C>> {
    Ok(GeneratorCommitments([<C as Curve>::read_G(reader)?, <C as Curve>::read_G(reader)?]))
  }
```

**File:** crypto/frost/src/nonce.rs (L133-139)
```rust
  pub(crate) fn read<R: Read>(reader: &mut R, generators: &[Vec<C::G>]) -> io::Result<Self> {
    let nonces = (0 .. generators.len())
      .map(|i| NonceCommitments::read(reader, &generators[i]))
      .collect::<Result<Vec<NonceCommitments<C>>, _>>()?;

    Ok(Commitments { nonces })
  }
```

**File:** crypto/frost/src/nonce.rs (L194-209)
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
```

**File:** crypto/frost/src/algorithm.rs (L182-184)
```rust
  fn nonces(&self) -> Vec<Vec<C::G>> {
    vec![vec![C::generator()]]
  }
```

**File:** crypto/frost/src/algorithm.rs (L201-210)
```rust
  fn sign_share(
    &mut self,
    params: &ThresholdView<C>,
    nonce_sums: &[Vec<C::G>],
    mut nonces: Vec<Zeroizing<C::F>>,
    msg: &[u8],
  ) -> C::F {
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
```

**File:** networks/bitcoin/src/crypto.rs (L13-16)
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

**File:** networks/bitcoin/src/crypto.rs (L54-73)
```rust
  /// If either `R` or `A` is the point at infinity, this will panic.
  #[derive(Clone, Copy, Debug)]
  pub struct Hram;
  #[allow(non_snake_case)]
  impl HramTrait<Secp256k1> for Hram {
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);

      let c = Scalar::reduce(U256::from_be_slice(Sha256::from_engine(data).as_ref()));
      // If the nonce was odd, sign `r - cx` instead of `r + cx`, allowing us to negate `s` at the
      // end to sign as `-r + cx`
      <_>::conditional_select(&c, &-c, needs_negation(R))
    }
```

**File:** crypto/frost/src/sign.rs (L276-280)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
```

**File:** crypto/frost/src/sign.rs (L283-299)
```rust
  fn sign(
    mut self,
    mut preprocesses: HashMap<Participant, Preprocess<C, A::Addendum>>,
    msg: &[u8],
  ) -> Result<(Self::SignatureMachine, SignatureShare<C>), FrostError> {
    let multisig_params = self.params.multisig_params();

    let mut included = Vec::with_capacity(preprocesses.len() + 1);
    included.push(multisig_params.i());
    for l in preprocesses.keys() {
      included.push(*l);
    }
    included.sort_unstable();

    // Included < threshold
    if included.len() < usize::from(multisig_params.t()) {
      Err(FrostError::InvalidSigningSet("not enough signers"))?;
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
