### Title
Malicious preprocess commitments force aggregate nonce `R` to the point at infinity, panicking every honest signer in `Hram::hram`/`x` — ([File: networks/bitcoin/src/crypto.rs])

### Summary
In the Bitcoin Taproot signing path, each participant's preprocess supplies arbitrary group-element commitments `D, E` per nonce via `Commitments::read` (`crypto/frost/src/nonce.rs:34-36`, `:133-139`). `read_G` accepts the canonical encoding of the identity point, so an attacker can submit `E = identity` and `D = -(sum of all other participants' bound nonces)`. Because `rho * E = rho * identity = identity` regardless of the binding factor `rho`, the attacker needs no knowledge of their own `rho` to make the session aggregate nonce `R = sum_l (D_l + rho_l * E_l)` equal the point at infinity (`BindingFactor::nonces`, `nonce.rs:194-211`). Every honest signer then computes `sign_share` → `IetfSchnorr` → `Hram::hram` → `x(&R)`, which calls `encoded.x().expect("point at infinity")` and panics (`crypto.rs:13-16`, `:59-72`). This is the Serai analog of the libsndfile heap over-read class: attacker-controlled public input reaches a code path that crashes on data the implementation assumed could not occur.

### Finding Description
- `read_preprocess` on `TransactionSignMachine` parses each co-signer's preprocess bytes with no identity/torsion checks on `GeneratorCommitments` (`send.rs:351-353`, `nonce.rs:34-36`).
- `Commitments::read`/`NonceCommitments::read` consume exactly `generators.len()` points per nonce, all attacker-controlled (`nonce.rs:74-80`, `:133-139`).
- In `AlgorithmSignMachine::sign`, `B.nonces(&nonces)` folds each participant's `D + rho * E` into `Rs` (`sign.rs:383`, `nonce.rs:180-211`). An attacker who is the last to send their preprocess observes all honest commitments, computes every `rho_l` from the public rho transcript (`sign.rs:362-371`), sets `E_l = identity` (annihilating their `rho_l` term) and `D_l` to the negation of the honest bound-nonce sum, yielding `R = identity`.
- `sign_share` for `bitcoin_serai::crypto::Schnorr` delegates to `FrostSchnorr` which calls `Hram::hram(R, A, m)`; `x(R)` panics on the point at infinity (`crypto.rs:13-22`, `:59-72`; also reachable via `p2tr_script_buf`/`x_only`).

### Impact Explanation
Any single participant in a threshold signing session can deterministically crash every honest signer's `sign()` call (Rust panic/abort) by submitting crafted preprocess commitments. For a multisig coordinating Bitcoin spends, a malicious or compromised co-signer — or any unprivileged party able to feed preprocess bytes into `read_preprocess`/`sign` — can repeatedly abort signing sessions, denying service indefinitely with zero cost. Severity: Medium (availability only; no key material is leaked, though the panic occurs after nonces are consumed, which can additionally complicate nonce-reuse hygiene on retry).

### Likelihood Explanation
Highly exploitable where an attacker holds or can influence one signing share in a session: the attack requires only choosing two group elements in a message they already send, with no computational work. The triggering condition (`R = identity`) is deterministic, not probabilistic, since the `rho_l * E_l` term vanishes when `E_l` is the identity. The code comment at `crypto.rs:79-80` claims malicious participants have only "a negligible probability" of hitting infinity, which is incorrect — the adversary controls the point directly.

### Recommendation
Reject identity (and, where applicable, small-order) points in `GeneratorCommitments::read`/`Commitments::read` in `crypto/frost/src/nonce.rs`. Additionally, make `x`/`x_only` in `networks/bitcoin/src/crypto.rs` return `Option`/`io::Result` and propagate a `FrostError` instead of panicking, so a malicious preprocess yields `FrostError::InvalidCommitments` rather than a crash. Consider also checking `B.nonces` results for identity before calling `sign_share`.

### Proof of Concept
```rust
// Attacker is participant A, last to broadcast preprocess in a t-of-n FROST
// session using TransactionSignMachine / AlgorithmMachine<Secp256k1, Schnorr>.
//
// 1. Collect honest preprocesses. For each honest participant l and nonce index n:
//      bound_l[n] = D_l + rho_l * E_l     // rho computable from the public transcript
// 2. Choose own commitments for nonce 0:
//      E_A = Secp256k1::generator() * Scalar::ZERO  // point at infinity
//      D_A = -(sum over honest l of bound_l[0])     // negated honest sum
//    Then D_A + rho_A * E_A = D_A (since rho_A * identity = identity), so
//      R = sum_l (D_l + rho_l * E_l) = identity.
// 3. Broadcast Preprocess { commitments: [[D_A, E_A]], addendum: () }.
//
// Every honest signer calls sign() -> B.nonces() -> R = identity ->
// FrostSchnorr::sign_share -> Hram::hram(&R, ...) -> x(&R)
// -> panic!("point at infinity")   // networks/bitcoin/src/crypto.rs:15
```
Reachability: `read_preprocess` accepts the attacker's serialized commitments without an identity check (`nonce.rs:34-36`, `:133-139`; `send.rs:351-353`), and `x`/`x_only` unconditionally `expect` a non-infinity point (`crypto.rs:13-22`). [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** crypto/frost/src/sign.rs (L383-398)
```rust
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
