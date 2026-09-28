### Title
BIP-340 signatures are emitted invalid whenever the aggregate nonce `R` has odd Y, because `s` is negated instead of the nonce - ([File: networks/bitcoin/src/crypto.rs](networks/bitcoin/src/crypto.rs))

### Summary
Analogous to the MerkleDistributor report — where the verification data omitted a field (`token`) so the claimed object didn't match what was verified — the Bitcoin `Schnorr::verify` implementation verifies a Schnorr signature against the aggregate nonce point `R`, then silently drops `R`'s sign byte and compensates by negating `s`. Negating `s` does not produce a signature valid under the implied even-`Y` nonce `-R`; the emitted transaction signature is invalid for ~50% of nonce sums, producing Bitcoin transactions the network will reject.

### Finding Description
`Schnorr::verify` in `networks/bitcoin/src/crypto.rs` builds `SchnorrSignature { R: nonces[0][0], s: sum }`, checks it internally via `sig.verify(group_key, self.c.unwrap())`, then post-processes it for Bitcoin: `sig.s = conditional_select(&sum, &-sum, needs_negation(&sig.R))` and serializes `sig.serialize()[1 ..]`, dropping `R`'s sign byte [1](#0-0) .

The internal Schnorr equation is `s·G = R + e·P` (`SchnorrSignature::sign(secret_share, nonce, c)` computes `s = r + c·x`, verified as `s·G == R + c·A` in `Schnorr::sign_share`/`verify`) [2](#0-1) .

On-chain, BIP-340 verification recomputes `R' = s'·G − e·P` and requires `x(R') == r` **and `R'` to have even Y**. If `R` is odd, the even point with `x(R)` is `−R`, so the required scalar is `s' = e·a − r = s − 2r` — i.e., the *nonce* must be negated before the challenge term is added. Instead, the code emits `s' = −s`, for which `s'·G − e·P = −R − 2e·P`, which is neither `R` nor `−R`. The signature is therefore invalid whenever the aggregate nonce point is odd — and `sign_share` performs no parity check on `nonce_sums[0][0]` before using the nonce [3](#0-2) .

Note the internal `sig.verify(group_key, self.c.unwrap())` check runs on the *unnegated* `sum`, so `verify` returns `Some(..)` and `TransactionSignatureMachine::complete` happily embeds the invalid signature into the witness [4](#0-3) .

### Impact Explanation
Every input of every transaction produced by `TransactionMachine`/`TransactionSignMachine` has an independent ~1/2 probability of an odd-`Y` nonce sum (nonce parity is not controlled anywhere: `NonceCommitments::new` commits `generator * d, generator * e` directly [5](#0-4) ). An n-input transaction is entirely unbroadcastable with probability `1 − 2⁻ⁿ`. The emitted signature bytes fail Bitcoin's BIP-340 verification, so the multisig cannot spend its UTXOs through this code path — funds are received by the Scanner but the produced spends are rejected by the network. This matches the report's class: the artifact that passes internal verification is not the artifact submitted for external verification, because a verified field (`R`'s parity) is dropped without recomputing the dependent value.

### Likelihood Explanation
Deterministic, probability ~1/2 per signature — it requires no attacker action at all; any honest signing session hits it. The only mitigation is retrying the full preprocess/sign round with fresh nonces until an even-`Y` `R` occurs (`SignableTransaction` being "clone-able across attempts" hints retries were anticipated), but nothing in `send.rs` checks parity or retries.

### Recommendation
Negate the nonce, not the response. In `Schnorr::sign_share` (or the wrapping `networks/bitcoin/src/crypto.rs` algorithm), check `needs_negation(&nonce_sums[0][0])`; if `R` is odd, negate the nonce scalar before computing the share (`s_i = −r_i + e·λ_i·a_i`), so that the summed signature satisfies `s·G = −R + e·P` and verifies against the even-`Y` lift of `x(R)`. The post-hoc `conditional_select(&sum, &-sum, ..)` in `verify` should then be removed (or kept only as a defensive assert that `R` is even). Additionally, validate the emitted 64-byte signature against BIP-340 (x-only `R`, x-only `P`) inside `verify` before returning it, so malformed-for-Bitcoin signatures are caught instead of being written into the witness.

### Proof of Concept
1. Build a `SignableTransaction` via `SignableTransaction::new` and `multisig(&keys)` for any valid `ThresholdKeys<Secp256k1>` and inputs [6](#0-5) .
2. Run `preprocess`/`sign`/`complete` repeatedly (fresh preprocess seeds → fresh aggregate nonces `Rs = B.nonces(&nonces)` [7](#0-6) ).
3. For any attempt where `nonces[0][0]` (the aggregate `R`) has odd `Y` — which occurs with probability ~1/2 per input — inspect the emitted witness: `sig.s` was set to `−sum` while `r = x(R)` is unchanged [8](#0-7) .
4. Verify the emitted `(r, s')` under BIP-340 with `e = hashBIP340(r ∥ x(P) ∥ sighash)` and `P` the tweaked even group key: `s'·G − e·P = −R − 2eP`, whose x-coordinate equals `x(R)` only with negligible probability. The transaction is rejected by any Bitcoin node (`SCRIPT_ERR_SCHNORR_SIG`), despite `TransactionSignatureMachine::complete` having returned `Ok(tx)`.

Caveat: this conclusion assumes `SchnorrSignature::sign` uses the standard `s = r + c·x` convention verified as `s·G == R + c·A` (as indicated by `crypto/frost/src/algorithm.rs:208-216`). If the `schnorr` crate instead uses `s = r − c·x` with verification `s·G == R − c·A`, the negation is coincidentally correct and this finding would not hold; the sign/verify convention in `crypto/schnorr` should be confirmed.

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L273-285)
```rust
  pub fn multisig(self, keys: &ThresholdKeys<Secp256k1>) -> Option<TransactionMachine> {
    let mut sigs = vec![];
    for i in 0 .. self.tx.input.len() {
      let offset = keys.clone().offset(self.offsets[i]);
      if p2tr_script_buf(offset.group_key())? != self.prevouts[i].script_pubkey {
        None?;
      }

      sigs.push(AlgorithmMachine::new(Schnorr::new(), keys.clone().offset(self.offsets[i])));
    }

    Some(TransactionMachine { tx: self, sigs })
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L417-425)
```rust
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
    }
```

**File:** crypto/frost/src/nonce.rs (L63-71)
```rust
    let mut commitments = Vec::with_capacity(generators.len());
    for generator in generators {
      commitments.push(GeneratorCommitments([
        *generator * nonce.0[0].deref(),
        *generator * nonce.0[1].deref(),
      ]));
    }

    (nonce, NonceCommitments { generators: commitments })
```

**File:** crypto/frost/src/sign.rs (L382-398)
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
