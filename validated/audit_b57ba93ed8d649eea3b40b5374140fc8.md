### Title
`SignableTransaction::new` does not deduplicate inputs, so a duplicated `ReceivedOutput` double-counts `input_sat` and produces a threshold-signed transaction Bitcoin consensus rejects - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the Maia report — where `retryDeposit` re-validated `msg.value` against `MIN_FALLBACK_RESERVE` without crediting the already-deposited gas — `SignableTransaction::new` performs its funds-sufficiency check against a naïve sum of the supplied inputs (`input_sat`) without accounting for which inputs were already counted. Passing the same `ReceivedOutput` (same `OutPoint`) twice counts its value twice, lets `input_sat < payment_sat + needed_fee` pass when it should fail, and yields a transaction with duplicate `TxIn`s that Bitcoin consensus rejects (`CheckTransaction` duplicate-input rule), yet the threshold still signs it.

### Finding Description
`SignableTransaction::new` accepts a caller-supplied `Vec<ReceivedOutput>` and immediately sums their values into `input_sat` and builds `tx_ins` from every entry — there is no uniqueness check on `input.outpoint`:

```rust
// networks/bitcoin/src/wallet/send.rs
let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
let offsets = inputs.iter().map(|input| input.offset).collect();
let tx_ins = inputs
  .iter()
  .map(|input| TxIn {
    previous_output: input.outpoint,
    ...
``` [1](#0-0) 

The solvency check then trusts this inflated sum:

```rust
if input_sat < (payment_sat + needed_fee) {
  Err(TransactionError::NotEnoughFunds { ... })?;
}
``` [2](#0-1) 

Because `ReceivedOutput` is deserializable from untrusted bytes via `ReceivedOutput::read` (which performs no outpoint validation), a duplicate outpoint can be injected into the input list. The resulting `SignableTransaction` passes all internal checks, `multisig()` builds an `AlgorithmMachine` per input (the duplicate key/offset check passes since the offset is identical), and `TransactionSignMachine::sign` produces a valid `TapSighashType::Default` sighash for each duplicated input position using `Prevouts::All`, so the FROST threshold produces valid-looking signatures over an invalid transaction. [3](#0-2) [4](#0-3) 

### Impact Explanation
The threshold signs a transaction that can never be included in a block: Bitcoin's `CheckTransaction` rejects any transaction whose `vin` contains a repeated outpoint. Concretely, the real funds backing the duplicated input remain unspent on-chain but the signed plan cannot be broadcast — the batch of payments the signature was meant to authorize is never executed, and the signing session/preprocesses are consumed. This is a "funds treated as allocated/spendable that are not spendable" outcome: the accounting check (`NotEnoughFunds`) was satisfied by double-counted value, so the system proceeds to sign a payment it cannot actually fund.

### Likelihood Explanation
The attacker-controlled reachability is narrow: the adversary must get a duplicated `ReceivedOutput` into the `inputs` vector. `ReceivedOutput::read` accepts arbitrary bytes including a repeated outpoint, and no later stage re-derives or deduplicates inputs — `prevouts` are stored as-supplied and committed via `Prevouts::All`. Any pipeline where inputs are relayed as serialized `ReceivedOutput`s (rather than re-scanned from confirmed blocks by the same party constructing the transaction) exposes this. Within the wallet crate itself, construction from honestly-scanned outputs cannot produce duplicates, so severity is bounded to a signing-round DoS / stuck-payment rather than theft: the duplicated input's coins remain owned by the wallet and a corrected transaction can spend them. Medium severity.

### Recommendation
Enforce input uniqueness in `SignableTransaction::new`: insert each `input.outpoint` into a `HashSet` and return an error (e.g., a new `TransactionError::DuplicateInput`) if it is already present, before computing `input_sat` or building `tx_ins`. Alternatively, derive inputs exclusively from `Scanner`-produced `ReceivedOutput`s keyed by outpoint and deduplicate at deserialization boundaries where untrusted `ReceivedOutput`s are aggregated.

### Proof of Concept
1. An output `O` of value `v` pays to the wallet's tweaked key at `outpoint` `P`. `input_sat` alone (`v`) is insufficient for `payment_sat + needed_fee`.
2. An attacker supplies `inputs = [ReceivedOutput{offset, O, P}, ReceivedOutput{offset, O, P}]` (e.g., via `ReceivedOutput::read` on attacker-controlled bytes that repeat the serialized record).
3. `input_sat = 2v >= payment_sat + needed_fee`, so the `NotEnoughFunds` check passes; `tx_ins` contains two `TxIn`s with identical `previous_output = P`.
4. `multisig(keys)` succeeds (offset check passes for both positions), `preprocess`/`sign` produce per-input Schnorr shares over `taproot_key_spend_signature_hash`, and `complete` returns a signed `Transaction`.
5. Broadcasting fails: the transaction violates Bitcoin's duplicate-input consensus rule and is rejected as a non-consensus-invalid (bad-txns-inputs-duplicate) transaction. The payments are never made despite a fully-signed artifact, and the consumed preprocesses/signing round are wasted.

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L175-185)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();
```

**File:** networks/bitcoin/src/wallet/send.rs (L215-221)
```rust
    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
```
