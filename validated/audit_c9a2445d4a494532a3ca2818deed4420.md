### Title
Bitcoin threshold signatures omit BIP-340 public-key parity handling, making funds unspendable when the tweaked key is odd - ([File: `networks/bitcoin/src/crypto.rs`])

### Summary
Serai’s BIP-340 FROST adapter normalizes an odd nonce `R` by negating the challenge and final `s`, but it never normalizes an odd aggregate or tweaked public key `A`. BIP-340 interprets `A` as the even-Y lift of its x-coordinate, so signatures produced for an odd-Y internal key are invalid on Bitcoin. Any output whose internal key is odd can be detected as spendable by the wallet while the threshold signing path produces an invalid witness.

### Finding Description
`Hram::hram` hashes only `x(R)`, `x(A)`, and the message, then conditionally negates the challenge based on `R`’s parity. [1](#0-0)  The adapter’s `verify` similarly conditionally negates only `s` based on `R`’s parity before truncating the signature serialization. [2](#0-1) 

There is no corresponding adjustment of the secret side when `A` has odd Y. For BIP-340, an x-only public key denotes the even-Y representative; if the internally represented point `A` is odd, the effective private key is `-x`, not `x`. The code still calculates and verifies shares against the odd internal `group_key`, so the completed scalar is valid under `A` but invalid under BIP-340’s even-lifted key.

The Bitcoin transaction path applies the per-input offset, compares the resulting tweaked key’s P2TR script to the previous output, and creates a signing machine with that tweaked `ThresholdKeys`. [3](#0-2)  It then signs the Taproot key-spend sighash and inserts the resulting 64-byte signature into the witness. [4](#0-3) 

### Impact Explanation
Funds sent to a P2TR output whose untweaked-or-offset internal key is odd are reported as controlled funds, but threshold signing produces a BIP-340-invalid signature. Because public offsets can make the internal point odd with roughly one-half probability, an unprivileged payer can trigger this by paying to an affected derived address; subsequently, the wallet cannot validly spend that output. This is not merely a compatibility issue: the completed signature’s verification equation is for the wrong representative of the x-only key.

### Likelihood Explanation
The condition is reached whenever `needs_negation(group_key)` is true. Since offsets and aggregate keys are effectively uniform curve points, roughly half of otherwise-valid addresses/transactions encounter the bug. No malicious validator, leaked key, invalid encoding, or unsafe dependency is required; the only attacker input needed is a Bitcoin payment to an affected output script.

### Recommendation
Normalize the private-side equation for odd internal keys. When `needs_negation(group_key)` is true, use the negated effective group secret/share—or equivalently negate the completed `s` contribution for the public-key parity in addition to handling `R` parity—so the signature verifies for the even-Y BIP-340 key. Add deterministic tests covering all four combinations of odd/even aggregate key and aggregate nonce, including an externally checked BIP-340 verification.

### Proof of Concept
1. Construct a `ThresholdKeys<Secp256k1>` or offset variant whose `group_key()` has odd Y.
2. Create a `ReceivedOutput` paying `p2tr_script_buf(keys.group_key())` and a `SignableTransaction` spending it.
3. Run `multisig`, `preprocess`, `sign`, and `complete`.
4. The internal FROST verifier accepts the share equation against the odd `ProjectivePoint`, while the emitted witness fails BIP-340 verification because Bitcoin treats `x(A)` as the even-Y key.

The missing parity check is localized: `Hram` checks `needs_negation(R)` but never `needs_negation(A)`, and finalization negates `s` only when `R` is odd. [1](#0-0) [2](#0-1)

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

**File:** networks/bitcoin/src/crypto.rs (L145-149)
```rust
      self.0.verify(group_key, nonces, sum).map(|mut sig| {
        sig.s = <_>::conditional_select(&sum, &-sum, needs_negation(&sig.R));
        // Convert to a Bitcoin signature by dropping the byte for the point's sign bit
        sig.serialize()[1 ..].try_into().unwrap()
      })
```

**File:** networks/bitcoin/src/wallet/send.rs (L273-284)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L373-424)
```rust
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
        )?;
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
  }
}

pub struct TransactionSignatureMachine {
  tx: Transaction,
  sigs: Vec<AlgorithmSignatureMachine<Secp256k1, Schnorr>>,
}

impl SignatureMachine<Transaction> for TransactionSignatureMachine {
  type SignatureShare = Vec<SignatureShare<Secp256k1>>;

  fn read_share<R: Read>(&self, reader: &mut R) -> io::Result<Self::SignatureShare> {
    self.sigs.iter().map(|sig| sig.read_share(reader)).collect()
  }

  fn complete(
    mut self,
    mut shares: HashMap<Participant, Self::SignatureShare>,
  ) -> Result<Transaction, FrostError> {
    for (input, schnorr) in self.tx.input.iter_mut().zip(self.sigs.drain(..)) {
      let sig = schnorr.complete(
        shares.iter_mut().map(|(l, shares)| (*l, shares.remove(0))).collect::<HashMap<_, _>>(),
      )?;

      let mut witness = Witness::new();
      witness.push(sig);
      input.witness = witness;
```
