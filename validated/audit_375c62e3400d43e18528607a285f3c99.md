### Title
FROST "BIP-340" signature accepted by internal `verify` is invalid on-chain when the group key has odd Y — ([File: networks/bitcoin/src/crypto.rs](https://github.com))

### Summary
The bug class from the external report — "a check returns success for an input that the real verifier will reject, because the success path is short-circuited/approximated" — maps directly onto `bitcoin_serai::crypto::Schnorr::verify` / `Hram::hram`. The FROST `Algorithm::verify` path plays the role of the simulation: it internally confirms the share sum satisfies `s·G = R + c·A` and emits the 64-byte signature, but only corrects for the parity of `R`. BIP-340 on-chain verification recomputes the challenge over `x(R) ‖ x(A_even) ‖ m` and checks `s·G = R_even + c·A_even`, forcing **both** `R` and `A` to their even-Y representatives. When the FROST group key `A` has odd Y, the emitted signature fails on-chain even though `Algorithm::verify` returned `Some`.

### Finding Description
`Hram::hram` computes `c = H_BIP340(x(R), x(A), m)` and then applies `conditional_select(&c, &-c, needs_negation(R))` — i.e., it negates the challenge iff the nonce point `R` is odd [1](#0-0) . `sign_share` then produces `s = r + c'·a` via the inner FROST Schnorr sign [2](#0-1) . `verify` reconstructs the signature, checks it against the *actual* (possibly odd) `R` and `A` under the stored challenge `c'`, and on success serializes `[1..]`, dropping the R parity byte, and flips `s` iff `R` is odd [3](#0-2) .

The math: internal check is `s·G = R + c'·A` with `c' = -c` when `R` odd, so the output `s' = -s` satisfies `s'·G = -R + c·A`. On-chain BIP-340 uses `A_even` and `R_even`:
- `R` odd, `A` even: needs `-R + c·A` ✓
- `R` odd, `A` odd: needs `-R - c·A` ✗ (code produces `-R + c·A`)
- `R` even, `A` odd: needs `R - c·A` ✗ (code produces `s·G = R + c·A`, and no negation is triggered since `R` is even)

There is no normalization of the group key to even Y (nor negation of the secret share, the standard fix) anywhere in `Schnorr::sign_share`, `Hram::hram`, or `verify` in this file; `params.group_key()`/`params.secret_share()` are used raw [4](#0-3) . Since a FROST group key is an arbitrary curve point, it has odd Y with probability ~1/2.

### Impact Explanation
`AlgorithmSignatureMachine::complete` calls `self.params.algorithm.verify(...)` as the acceptance gate [5](#0-4) . For an odd-Y group key it returns `Some(sig_bytes)`, so the signer set believes a valid Bitcoin signature was produced — but every Bitcoin node will reject it under BIP-340. For a threshold-controlled output this means the multisig emits an unspendable signature for that message: a full signing session is consumed and funds cannot be moved by that signature, i.e., signature bytes reported valid that are not valid on-chain (Medium/High, DoS of the signing output rather than key compromise).

### Likelihood Explanation
Purely a function of group-key parity: ~50% of generated keys are affected, deterministic once the key exists. Not attacker-triggered, but no malicious party is required — it is a correctness defect in the verifier/sign path reachable via ordinary public inputs (the message signed).

### Recommendation
When the group key `A` is odd, negate the effective secret share (equivalently, treat the key as `A_even = -A` and sign with `-a`) before/inside `sign_share`, and make `verify` validate against `R_even`/`A_even` so internal acceptance coincides with BIP-340 acceptance. Alternatively enforce even-Y group keys at key-generation/offset time and assert `!needs_negation(group_key)` in this algorithm.

### Proof of Concept
Let `a` be the group secret with `A = a·G` odd-Y, `r` the combined nonce with `R = r·G` even-Y. `hram` leaves `c' = c` (R even). Output `s = r + c·a`. Internal verify: `s·G = R + c·A` ✓ → `Some` returned. On-chain BIP-340 checks `s·G =? R_even + c·A_even = R - c·A`, but `s·G = R + c·A` — fails whenever `c·a ≠ 0`. Same failure for `R` odd (produced `-R + c·A` vs required `-R - c·A`). The internal verifier never models the `A → A_even` substitution, so it cannot observe the discrepancy. (Caveat: whether upstream code normalizes the Secp256k1 group key to even Y before instantiating this `Algorithm` could not be fully confirmed from the in-scope files read; the defect is proven within `networks/bitcoin/src/crypto.rs` itself.)

### Citations

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

**File:** networks/bitcoin/src/crypto.rs (L138-150)
```rust
    #[must_use]
    fn verify(
      &self,
      group_key: ProjectivePoint,
      nonces: &[Vec<ProjectivePoint>],
      sum: Scalar,
    ) -> Option<Self::Signature> {
      self.0.verify(group_key, nonces, sum).map(|mut sig| {
        sig.s = <_>::conditional_select(&sum, &-sum, needs_negation(&sig.R));
        // Convert to a Bitcoin signature by dropping the byte for the point's sign bit
        sig.serialize()[1 ..].try_into().unwrap()
      })
    }
```

**File:** crypto/frost/src/algorithm.rs (L201-217)
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
  }

  #[must_use]
  fn verify(&self, group_key: C::G, nonces: &[Vec<C::G>], sum: C::F) -> Option<Self::Signature> {
    let sig = SchnorrSignature { R: nonces[0][0], s: sum };
    Some(sig).filter(|sig| sig.verify(group_key, self.c.unwrap()))
  }
```

**File:** crypto/frost/src/sign.rs (L462-467)
```rust
    // Perform signature validation instead of individual share validation
    // For the success route, which should be much more frequent, this should be faster
    // It also acts as an integrity check of this library's signing function
    if let Some(sig) = self.params.algorithm.verify(self.view.group_key(), &self.Rs, sum) {
      return Ok(sig);
    }
```
