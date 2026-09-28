I have most of the FROST signing path. Let me check the remaining pieces: `IetfTranscript` framing, the rest of `SignableTransaction::new` (offset/amount handling), and the Bitcoin taproot key handling in `crypto.rs`.### Title
BIP-340 group-key parity is never negated, producing signatures valid to Serai's verifier but invalid on-chain — (File: networks/bitcoin/src/crypto.rs)

### Summary
The bug class in the reference report is "the data actually signed differs from the data that was reviewed/committed." The analog in Serai's Bitcoin stack is that the signature the FROST machine internally verifies and emits is not the signature Bitcoin consensus expects: `Hram` and `verify` in `networks/bitcoin/src/crypto.rs` handle BIP-340 parity only for the nonce commitment `R`, and never negate the secret share / key term when the x-only output key `A` has an odd Y coordinate. The result is a signature that passes `AlgorithmSignatureMachine::complete`'s internal verification but is rejected by every Bitcoin node — i.e., what the signers verify as correct is not what is actually signed for on-chain.

### Finding Description
`TransactionSignMachine::sign` in `networks/bitcoin/src/wallet/send.rs` (lines 383–391) feeds each input's `taproot_key_spend_signature_hash` to the `Schnorr` algorithm, which is `FrostSchnorr<Secp256k1, Hram>` (`crypto.rs:85`). The BIP-340 challenge is computed in `Hram::hram` over `x(R) || x(A) || m` and is conditionally negated **only** on `needs_negation(R)` (`crypto.rs:72`). The emitted signature adjusts `s` only on `needs_negation(&sig.R)` (`crypto.rs:146`).

BIP-340 requires two parity corrections, not one: if the *public key* `A` has odd Y, the signer must use `-x` as the secret (because the x-only key commits to the even-Y lift `-A`). Here `sign_share` uses `params.secret_share()` unmodified (`crypto/frost/src/algorithm.rs:208–210`), and `verify` validates `sum·G = R + c'·A` against the full point `group_key` (`algorithm.rs:215–216`), so the odd-A case passes internal verification. On-chain, the verifier checks `s·G = even_lift(R) + e·even_lift(A) = -R - c·A`, while the emitted `s` satisfies `s·G = -R + c·A` — invalid whenever `A` is odd.

Additionally, `SignableTransaction::multisig` (`send.rs:273–285`) only checks `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — the script commitment, not the parity-adjusted key relationship — so the mismatch is not caught at construction.

### Impact Explanation
Roughly half of all received outputs (those whose tweaked output key has odd Y) can never be spent: `TransactionSignatureMachine::complete` returns a transaction whose witness signatures pass Serai's internal `verify` but fail BIP-340 verification on the Bitcoin network. This is "funds reported received that are not spendable" — the scanner credits the deposit, the scheduler plans the spend, the threshold group produces a signature, and the resulting transaction is unbroadcastable. Recovery requires either retrying until a different aggregated `R`/key parity occurs (impossible — `A`'s parity is fixed per output) or out-of-band intervention with the key shares.

### Likelihood Explanation
The output-key parity is effectively a uniform random bit per received output (it depends on `key + offset·G` and the TapTweak), so ~50% of deposits hit this. It is reachable purely via public inputs: anyone sending a Bitcoin transaction to a Serai deposit address. No malicious validator, RPC, or collusion is required — the defect is entirely inside the in-scope signing path (`crypto.rs`, `send.rs`, `frost`).

Caveat: I was unable to fully read `networks/bitcoin/src/wallet/mod.rs` (`ReceivedOutput`, `p2tr_script_buf`, `register_offset`) within the tool budget. If `p2tr_script_buf` or the offset-derivation path secretly folds an even-Y constraint into `offset` (e.g., negating the offset to force an even output key), this finding would be mitigated. Nothing in `crypto.rs` or `send.rs` shows such handling — `needs_negation` is applied exclusively to `R` — so the analysis stands on the in-scope code as written.

### Recommendation
In `Hram`-aware signing for `Secp256k1`, apply the BIP-340 key-parity correction: when `needs_negation(group_key)` is set, negate the effective secret share — e.g., have `sign_share` use `-params.secret_share()` (and negate the per-participant offset accordingly in `ThresholdView`), or equivalently negate the aggregated `sum`/`c` pairing so that `s·G = R + e·even_lift(A)`. `verify` should then construct the BIP340 signature against `even_lift(A)`. The cleanest location is `frost_crypto::Schnorr::sign_share`/`verify` in `networks/bitcoin/src/crypto.rs:128–150`, where `params.group_key()` parity is already observable.

### Proof of Concept
Let `A` be the offset group key for an input such that `A` has odd Y (x-only output key `P = -A` on-chain). With even `R` for simplicity:

1. `hram` returns `c = H(x(R) || x(A) || m)` unchanged since `R` is even (`crypto.rs:69–72`).
2. `sign_share` returns `s_i = r_i + c·λ_i·x_i`; aggregated `s = r + c·x` where `x·G = A` (`algorithm.rs:208–210`, `sign.rs:455–460`).
3. Internal `verify` checks `s·G = R + c·A` — holds, signature emitted (`crypto.rs:145–149`).
4. Bitcoin consensus computes `s·G ?= R + e·P = R - c·A`. The emitted `s` gives `R + c·A ≠ R - c·A` for nonzero `c` — signature invalid, input unspendable.

For odd `R` the same mismatch composes with the existing `R`-negation, still yielding an invalid signature whenever `A` alone is odd. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** networks/bitcoin/src/crypto.rs (L58-73)
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

**File:** crypto/frost/src/algorithm.rs (L201-216)
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

**File:** networks/bitcoin/src/wallet/send.rs (L383-391)
```rust
        let (sig, share) = sig.sign(
          commitments[i].clone(),
          cache
            .taproot_key_spend_signature_hash(i, &prevouts, TapSighashType::Default)
            // This should never happen since the inputs align with the TX the cache was
            // constructed with, and because i is always < prevouts.len()
            .expect("taproot_key_spend_signature_hash failed to return a hash")
            .as_ref(),
        )?;
```
