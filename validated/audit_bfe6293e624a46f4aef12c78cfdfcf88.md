### Title
Malicious BIP-340 nonce commitment collapses aggregate `R` and panics signing - ([File: crypto/frost/src/nonce.rs](crypto/frost/src/nonce.rs))

### Summary

A malicious FROST participant can submit a crafted nonce-commitment pair whose contribution cancels all other participants’ nonce commitments. `Ciphersuite::read_G` accepts the canonical identity encoding, and the Bitcoin BIP-340 algorithm later calls `x(R)` on the resulting aggregate nonce point. When `R` is the point at infinity, `x` panics, causing a remotely triggerable denial of service during threshold signing. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description

FROST preprocess messages contain nonce commitments `(D, E)` for each required nonce. `AlgorithmSignMachine::read_preprocess` deserializes those commitments using `Commitments::read`, which reads two canonical group elements per generator without rejecting the identity. [4](#0-3) [5](#0-4) [6](#0-5) 

During `AlgorithmSignMachine::sign`, all submitted bound commitments are summed as `D_i + rho_i * E_i` to form the aggregate nonce point `R`. [2](#0-1) 

An attacker who waits for the other participants’ preprocesses can set:

```text
E_malicious = identity
D_malicious = -sum(D_i + rho_i * E_i for all other participants)
```

Because `E_malicious` is the identity, the attacker does not need to predict their binding factor `rho_malicious`. Their effective contribution remains `D_malicious`, making the final aggregate `R` the identity. `read_G` permits this encoding because it checks only canonical deserialization and canonical re-encoding; it does not reject `is_identity()`. [1](#0-0) [7](#0-6) 

The Bitcoin FROST algorithm then computes the BIP-340 challenge through `Hram::hram`, which calls `x(R)`. `x` unconditionally unwraps the x-coordinate and panics when the point is infinity. [8](#0-7) [3](#0-2) 

### Impact Explanation

A single malicious signing participant can crash every honest participant that accepts its crafted preprocess and reaches `sign`. This aborts the signing operation and may terminate the processor or validator task rather than returning a `FrostError` that can be handled and attributed. The attack requires only control of public preprocess bytes supplied to `read_preprocess`/`sign`; it does not require a key share, invalid curve encodings, unsafe code, or consensus misconduct beyond sending a malformed protocol message. [9](#0-8) [10](#0-9) 

The in-scope impact is a reliable remote denial of service analogous to the reported assertion-failure bug: attacker-controlled input reaches an implicit invariant that an aggregate nonce is never infinity. [11](#0-10) 

### Likelihood Explanation

Likelihood is moderate. The attacker must be a recognized participant in the selected FROST signing set and must be able to send its preprocess after learning the other participants’ commitments, or otherwise arrange for its commitment to cancel the aggregate. In asynchronous or coordinator-mediated signing flows where preprocesses are collected in a map, that ordering is plausible. Once received, triggering the panic is deterministic because the malicious point contribution is chosen arithmetically rather than probabilistically. [12](#0-11) [2](#0-1) 

### Recommendation

Reject identity nonce commitments and, more robustly, reject an identity aggregate nonce before invoking algorithm-specific signing or verification. Concretely:

- In `GeneratorCommitments::read` or `Commitments::read`, reject canonical encodings of `C::G::identity()`.
- In `BindingFactor::nonces`, return an error if any computed aggregate nonce is identity, requiring the method and its callers to propagate a `FrostError` instead of indexing/panicking later.
- In Bitcoin `Hram::hram`, avoid `x()` on unchecked points or ensure the caller has already proven `R` and `A` are non-identity.
- Add a regression test where a malicious preprocess contributes `E = identity` and `D = -sum(other bound D values)`, asserting a normal `InvalidPreprocess`/`InvalidParticipant` error rather than a panic.

### Proof of Concept

Conceptual reproduction for a two-of-two signing session:

```rust
// Victim preprocess contains nonce commitments:
//   D_v = g^d_v
//   E_v = g^e_v
//
// Attacker constructs:
//   D_m = -D_v
//   E_m = identity
//
// Their bound commitment is D_m + rho_m * E_m = D_m.
// Therefore aggregate R = D_v + D_m = identity.
```

The concrete byte-level trigger is:

1. Parse the victim’s preprocess with `AlgorithmSignMachine::read_preprocess`. [4](#0-3) 
2. Encode `D_m = -(D_victim)` and `E_m = identity` as the attacker’s `GeneratorCommitments`. `GeneratorCommitments` serializes exactly these two points. [13](#0-12) 
3. Submit the malicious preprocess to `AlgorithmSignMachine::sign`.
4. `BindingFactor::nonces` computes `R = identity`. [2](#0-1) 
5. `networks/bitcoin/src/crypto.rs` passes `R` into `x`, whose `expect("point at infinity")` panics. [14](#0-13) [3](#0-2)

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

**File:** crypto/frost/src/nonce.rs (L34-41)
```rust
  fn read<R: Read>(reader: &mut R) -> io::Result<GeneratorCommitments<C>> {
    Ok(GeneratorCommitments([<C as Curve>::read_G(reader)?, <C as Curve>::read_G(reader)?]))
  }

  fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.0[0].to_bytes().as_ref())?;
    writer.write_all(self.0[1].to_bytes().as_ref())
  }
```

**File:** crypto/frost/src/nonce.rs (L74-79)
```rust
  fn read<R: Read>(reader: &mut R, generators: &[C::G]) -> io::Result<NonceCommitments<C>> {
    Ok(NonceCommitments {
      generators: (0 .. generators.len())
        .map(|_| GeneratorCommitments::read(reader))
        .collect::<Result<_, _>>()?,
    })
```

**File:** crypto/frost/src/nonce.rs (L180-189)
```rust
  pub(crate) fn bound(&self, l: Participant) -> Vec<Vec<C::G>> {
    let mut res = vec![];
    for (i, (nonce, rho)) in
      self.0[&l].commitments.nonces.iter().zip(self.binding_factors(l).iter()).enumerate()
    {
      res.push(vec![]);
      for generator in &nonce.generators {
        res[i].push(generator.0[0] + (generator.0[1] * rho));
      }
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

**File:** networks/bitcoin/src/crypto.rs (L13-16)
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

**File:** networks/bitcoin/src/crypto.rs (L59-73)
```rust
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

**File:** networks/bitcoin/src/crypto.rs (L76-83)
```rust
  /// BIP-340 Schnorr signature algorithm.
  ///
  /// This may panic if called with nonces/a group key which are the point at infinity (which have
  /// a negligible probability for a well-reasoned caller, even with malicious participants
  /// present).
  ///
  /// `verify`, `verify_share` MUST be called after `sign_share` is called. Otherwise, this library
  /// MAY panic.
```

**File:** crypto/frost/src/sign.rs (L276-280)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
```

**File:** crypto/frost/src/sign.rs (L283-313)
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
    }
    // OOB index
    if u16::from(included[included.len() - 1]) > multisig_params.n() {
      Err(FrostError::InvalidParticipant(multisig_params.n(), included[included.len() - 1]))?;
    }
    // Same signer included multiple times
    for i in 0 .. (included.len() - 1) {
      if included[i] == included[i + 1] {
        Err(FrostError::DuplicatedParticipant(included[i]))?;
      }
    }

    let view = self.params.keys.view(included.clone()).unwrap();
    validate_map(&preprocesses, &included, multisig_params.i())?;
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
