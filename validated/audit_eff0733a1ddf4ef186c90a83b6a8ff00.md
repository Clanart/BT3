### Title
Aggregate nonce point at infinity panics `Hram::hram` during BIP-340 share signing - (File: networks/bitcoin/src/crypto.rs)

### Summary
The bug class in CVE-2023-0217 is a crash (denial of service) triggered when an untrusted, malformed key reaches a code path that dereferences/uses it without a validity guard. The Serai analog is the BIP-340 `Hram` implementation for the Bitcoin Schnorr FROST algorithm: `Hram::hram` computes the challenge as `H(x(R) || x(A) || m)` and `x()` unconditionally calls `.expect("point at infinity")` on the compressed point's x-coordinate. `R` is the aggregate nonce `nonce_sums[0][0]`, which is a sum of per-participant nonce commitments `D_i + rho_i·E_i` parsed from counterparty-supplied preprocesses. If that sum is the point at infinity, `sign_share` panics.

### Finding Description
`x()` extracts the x-coordinate via `to_encoded_point(true).x().expect("point at infinity")`, so any point at infinity aborts the process rather than returning an error [1](#0-0) . `x_only()` has the same panic on the identity point [2](#0-1) . `Hram::hram` calls `x(R)` and `x(A)` directly, and its own documentation states "If either `R` or `A` is the point at infinity, this will panic" [3](#0-2) .

`R` reaches `hram` through `Schnorr::sign_share` → `self.0.sign_share(params, nonce_sums, ...)` [4](#0-3) , which computes `H::hram(&nonce_sums[0][0], &params.group_key(), msg)` [5](#0-4) . `nonce_sums` is the linear combination of nonce commitments read from other participants' preprocesses via `read_preprocess` [6](#0-5)  and consumed by `TransactionSignMachine::sign` [7](#0-6) .

While `Curve::read_G` rejects individual identity commitments [8](#0-7) , no check rejects a *sum* equal to identity before it is fed to `x()`. The generic (non-Bitcoin) challenge path tolerates this; only the Bitcoin `Hram` panics, so this is a Bitcoin-signing-specific crash on an attacker-influenced group element.

### Impact Explanation
A panic inside `sign_share`/`verify` aborts the Bitcoin transaction signing session (and, in-process, the calling coordinator/processor thread). This is a liveness denial of service on the threshold wallet: the transaction cannot be completed while a triggering preprocess set is in play. No secret material is leaked.

### Likelihood Explanation
An unprivileged counterparty can choose their nonce commitments `D_j, E_j` after seeing everyone else's preprocesses. Making the aggregate `Σ(D_i + rho_i·E_i)` equal to identity requires solving `D_j + rho_j(D_j, E_j)·E_j = -T`, where `rho_j = hash_binding_factor` commits to the attacker's own commitments. Because `rho_j` is a random-oracle output over `(D_j, E_j)`, this is a fixpoint equation with no known efficient solving strategy — so a *deliberate* trigger is computationally infeasible, though nothing in the code structurally prevents it, and it can also arise with negligible probability from adversarial-adjacent inputs. The precondition weakness (sum-of-points unchecked against identity before a panicking x-coordinate extraction) is real; practical exploitability is low. Severity: Medium.

### Recommendation
Before evaluating `Hram::hram`, check `nonce_sums[0][0]` (and `params.group_key()`) for identity in `Schnorr::sign_share`/`verify` and return a `FrostError`/reject rather than panicking; alternatively make `x()` return `Option`/`io::Error` and propagate. A defense-in-depth check that the aggregate nonce is non-identity in `crypto/frost` signing (not only per-commitment) would close this class for all curves.

### Proof of Concept
```rust
// networks/bitcoin/src/crypto.rs
// Hram::hram(R, A, m) where R = nonce_sums[0][0] is the sum of
// counterparty-provided nonce commitments (D_i + rho_i * E_i).
// If a signer's preprocess set is constructed so that the aggregate
// nonce equals ProjectivePoint::IDENTITY:
//
//   let R = ProjectivePoint::IDENTITY;
//   Hram::hram(&R, &group_key, msg);
//
// then inside hram:
//   data.input(&x(R));
//   // x() -> key.to_encoded_point(true).x().expect("point at infinity")
//   // -> PANIC: "point at infinity"
//
// Reachable via TransactionSignMachine::sign ->
// AlgorithmSignMachine::sign -> Schnorr::sign_share -> Hram::hram,
// with nonce commitments obtained from read_preprocess on untrusted
// participant bytes. Individual identity points are rejected by
// Curve::read_G, but a non-identity pair (D,E) whose rho-weighted
// sum cancels the rest of the aggregate is not checked.
```

Caveat: I could not fully trace `AlgorithmSignMachine::sign`'s nonce-aggregation in `crypto/frost/src/sign.rs` within the available iterations, so whether any upstream aggregate-identity check already exists is unverified; none was found in the inspected code.

### Citations

**File:** networks/bitcoin/src/crypto.rs (L13-16)
```rust
fn x(key: &ProjectivePoint) -> [u8; 32] {
  let encoded = key.to_encoded_point(true);
  (*encoded.x().expect("point at infinity")).into()
}
```

**File:** networks/bitcoin/src/crypto.rs (L21-23)
```rust
pub(crate) fn x_only(key: &ProjectivePoint) -> XOnlyPublicKey {
  XOnlyPublicKey::from_slice(&x(key)).expect("x_only was passed a point which was infinity or odd")
}
```

**File:** networks/bitcoin/src/crypto.rs (L59-67)
```rust
    fn hram(R: &ProjectivePoint, A: &ProjectivePoint, m: &[u8]) -> Scalar {
      const TAG_HASH: Sha256 = Sha256::const_hash(b"BIP0340/challenge");

      let mut data = Sha256::engine();
      data.input(TAG_HASH.as_ref());
      data.input(TAG_HASH.as_ref());
      data.input(&x(R));
      data.input(&x(A));
      data.input(m);
```

**File:** networks/bitcoin/src/crypto.rs (L128-136)
```rust
    fn sign_share(
      &mut self,
      params: &ThresholdView<Secp256k1>,
      nonce_sums: &[Vec<<Secp256k1 as Ciphersuite>::G>],
      nonces: Vec<Zeroizing<<Secp256k1 as Ciphersuite>::F>>,
      msg: &[u8],
    ) -> <Secp256k1 as Ciphersuite>::F {
      self.0.sign_share(params, nonce_sums, nonces, msg)
    }
```

**File:** crypto/frost/src/algorithm.rs (L208-210)
```rust
    let c = H::hram(&nonce_sums[0][0], &params.group_key(), msg);
    self.c = Some(c);
    SchnorrSignature::<C>::sign(params.secret_share(), nonces.swap_remove(0), c).s
```

**File:** networks/bitcoin/src/wallet/send.rs (L351-353)
```rust
  fn read_preprocess<R: Read>(&self, reader: &mut R) -> io::Result<Self::Preprocess> {
    self.sigs.iter().map(|sig| sig.read_preprocess(reader)).collect()
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L364-384)
```rust
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
```

**File:** crypto/frost/src/curve/mod.rs (L125-131)
```rust
  fn read_G<R: Read>(reader: &mut R) -> io::Result<Self::G> {
    let res = <Self as Ciphersuite>::read_G(reader)?;
    if res.is_identity().into() {
      Err(io::Error::other("identity point"))?;
    }
    Ok(res)
  }
```
