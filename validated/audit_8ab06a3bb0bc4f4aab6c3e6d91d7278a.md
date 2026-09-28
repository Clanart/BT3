### Title
Attacker-controlled FROST nonce commitments can force an infinity aggregate nonce and panic Bitcoin transaction signing - (File: networks/bitcoin/src/crypto.rs)

### Summary
Bitcoin’s FROST `Schnorr` algorithm calls `x(R)` on the aggregate nonce. `x` explicitly panics when passed the point at infinity. A signing participant can supply nonce commitments whose second commitment is the identity, making their binding-factor term vanish, and whose first commitment negates the aggregate nonce contributed by the other participants. The resulting aggregate nonce is infinity, causing the local signer to panic during `sign`. [1](#0-0) [2](#0-1) 

### Finding Description
`TransactionSignMachine::sign` parses a vector of FROST preprocesses for each input and passes them into `AlgorithmSignMachine::sign`. [3](#0-2)  Each preprocess contains nonce commitments read as arbitrary group elements through `GeneratorCommitments::read` and `Commitments::read`. [4](#0-3) [5](#0-4) 

During signing, the aggregate nonce for generator `g` is calculated as:

```rust
D += commitments.nonces[n].generators[g].0[0];
statements.push((
  binding_factors.as_ref().unwrap()[n],
  commitments.nonces[n].generators[g].0[1],
));
nonces[n].push(D + multiexp_vartime(&statements));
```

This is the FROST expression `sum(D_j + rho_j * E_j)`. [6](#0-5)  If a participant submits `E_j = infinity`, their `rho_j * E_j` term is always infinity regardless of `rho_j`. After observing the other participants’ preprocesses, they can choose `D_j` as the negation of the other participants’ combined nonce contribution. The final `nonce_sums[0][0]` is then infinity.

`bitcoin::crypto::Schnorr::sign_share` delegates to the FROST Schnorr implementation, which calls `Hram::hram(&nonce_sums[0][0], &params.group_key(), msg)`. [7](#0-6)  The Bitcoin HRAm calls `x(R)`, which converts the point to a compressed SEC encoding and unwraps its x coordinate. For the point at infinity, `encoded.x()` is `None`, so `.expect("point at infinity")` aborts the process. [1](#0-0) [8](#0-7) 

### Impact Explanation
An unprivileged counterparty able to provide a FROST preprocess can reliably abort a Bitcoin signing session before a signature share is produced. Because the panic occurs inside `sign`, this is a reachable denial of service against threshold transaction signing rather than an isolated verification failure or misuse-only panic. [9](#0-8) 

### Likelihood Explanation
The malicious participant must supply a syntactically valid preprocess containing the identity point as the second nonce commitment and a first commitment chosen to negate the remaining aggregate nonce. In an asynchronous signing round, a last-moving participant can observe the other preprocesses before submitting their own. Identity and negated group elements are expressible in the commitment encoding accepted by `read_G`; the commitment reader performs no semantic rejection of the identity at this layer. [4](#0-3) 

### Recommendation
Reject identity points when reading FROST nonce commitments, or explicitly check the aggregate nonce before calling `Hram::hram` and return `FrostError::InvalidPreprocess`/`InvalidSigningSet` instead of panicking. At minimum, `bitcoin::crypto::Hram::hram` should not call the panicking `x` helper on attacker-influenced points. [4](#0-3) [1](#0-0) 

### Proof of Concept
For a two-party Bitcoin signing session with one nonce and one generator:

1. Victim sends a valid preprocess with nonce commitments `(D_v, E_v)`.
2. Attacker computes the victim’s bound contribution `B_v = D_v + rho_v * E_v`.
3. Attacker submits a preprocess whose commitments are:
   - `D_a = -B_v`
   - `E_a = ProjectivePoint::identity()`
4. The aggregate nonce becomes:

```text
D_v + rho_v * E_v + D_a + rho_a * E_a
= B_v - B_v + rho_a * infinity
= infinity
```

5. `TransactionSignMachine::sign` reaches `Hram::hram(&infinity, ...)`.
6. `x(&infinity)` executes `encoded.x().expect("point at infinity")` and panics.

Conceptual Rust fragment:

```rust
// Inside the attacker's serialized Preprocess<Secp256k1, ()>
let identity = <Secp256k1 as Ciphersuite>::G::identity();
let malicious_d = -victim_bound_nonce;
let malicious_e = identity;

// GeneratorCommitments is serialized as D || E.
let mut preprocess_bytes = Vec::new();
preprocess_bytes.extend_from_slice(malicious_d.to_bytes().as_ref());
preprocess_bytes.extend_from_slice(malicious_e.to_bytes().as_ref());
```

The victim reads this via `read_preprocess`, after which `sign` panics rather than returning `FrostError`. [10](#0-9) [11](#0-10)

### Citations

**File:** networks/bitcoin/src/crypto.rs (L13-22)
```rust
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

**File:** networks/bitcoin/src/crypto.rs (L58-72)
```rust
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
```

**File:** crypto/frost/src/nonce.rs (L33-41)
```rust
impl<C: Curve> GeneratorCommitments<C> {
  fn read<R: Read>(reader: &mut R) -> io::Result<GeneratorCommitments<C>> {
    Ok(GeneratorCommitments([<C as Curve>::read_G(reader)?, <C as Curve>::read_G(reader)?]))
  }

  fn write<W: Write>(&self, writer: &mut W) -> io::Result<()> {
    writer.write_all(self.0[0].to_bytes().as_ref())?;
    writer.write_all(self.0[1].to_bytes().as_ref())
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

**File:** crypto/frost/src/nonce.rs (L193-209)
```rust
  // Get the nonces for this signing session
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

**File:** networks/bitcoin/src/wallet/send.rs (L351-390)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
  }

  fn sign(
    mut self,
    commitments: HashMap<Participant, Self::Preprocess>,
    msg: &[u8],
  ) -> Result<(TransactionSignatureMachine, Self::SignatureShare), FrostError> {
    if !msg.is_empty() {
      panic!("message was passed to the TransactionSignMachine when it generates its own");
    }

    let commitments = (0 .. self.sigs.len())
      .map(|c| {
        commitments
          .iter()
          .map(|(l, commitments)| (*l, commitments[c].clone()))
          .collect::<HashMap<_, _>>()
      })
      .collect::<Vec<_>>();

    let mut cache = SighashCache::new(&self.tx.tx);
    // Sign committing to all inputs
    let prevouts = Prevouts::All(&self.tx.prevouts);

    let mut shares = Vec::with_capacity(self.sigs.len());
    let sigs = self
      .sigs
      .drain(..)
      .enumerate()
      .map(|(i, sig)| {
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
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

**File:** crypto/frost/src/sign.rs (L276-280)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    Ok(Preprocess {
      commitments: Commitments::read::<_>(reader, &self.params.algorithm.nonces())?,
      addendum: self.params.algorithm.read_addendum(reader)?,
    })
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
