### Title
`SignableTransaction::new` does not reject duplicate inputs, producing a consensus-invalid transaction the multisig will still sign - ([File: networks/bitcoin/src/wallet/send.rs](File: networks/bitcoin/src/wallet/send.rs))

### Summary

The external report describes a composability flaw: an action that can be invoked twice within one batch (`multicall` calling `withdraw` twice) applies its effect twice without a uniqueness check. The direct analog in Serai is `SignableTransaction::new`, which accepts an arbitrary `Vec<ReceivedOutput>` as inputs and never checks that the referenced outpoints are unique. A duplicated input is counted twice toward `input_sat`, is serialized into the transaction as two identical `TxIn`s, and is then signed per-input by the FROST `TransactionMachine`. Bitcoin consensus (`CheckTransaction`) rejects any transaction containing duplicate inputs, so the resulting fully-signed transaction can never be broadcast.

### Finding Description

`SignableTransaction::new` sums input values and builds `TxIn`s directly from the provided `inputs` vector with no dedup or duplicate-outpoint check [1](#0-0) . The `NotEnoughFunds` check uses this double-counted `input_sat`, so a caller who supplies the same `ReceivedOutput` twice can make an under-funded transaction pass validation [2](#0-1) . The change calculation is likewise computed against the inflated sum, so it may emit a change output larger than the real funds available [3](#0-2) .

`SignableTransaction::multisig` then happily creates one `AlgorithmMachine` per input — including the duplicate — and `TransactionSignMachine::sign` produces a valid signature share for each duplicated input's sighash [4](#0-3) . Nothing anywhere in the pipeline detects that two inputs reference the same `OutPoint`.

This is reachable with untrusted data: `ReceivedOutput` is deserializable via `ReceivedOutput::read`, so an integrator or data path feeding duplicated/parsed outputs into `SignableTransaction::new` (analogous to a duplicated `withdraw` call in a multicall) triggers it.

### Impact Explanation

The threshold group produces a complete, valid-looking signed `Transaction` that is permanently invalid under Bitcoin consensus (duplicate inputs are rejected at `CheckTransaction`). Every signing session consumed producing it is wasted, and — worse — the change accounting was computed against double-counted input value, so the "plan" this transaction encodes (payments + change) can never execute as constructed. The affected UTXOs remain on-chain, so funds are recoverable only by constructing and re-signing a corrected transaction, but any downstream logic that treats the signed plan as executed reports funds as moved when they were not, and the outputs appear consumed while the intended payments never happen.

### Likelihood Explanation

Exploitation requires a duplicated `ReceivedOutput` to reach `SignableTransaction::new`. The wallet `Scanner` derives `ReceivedOutput`s with unique `OutPoint`s, so honest single-block scanning won't produce duplicates [5](#0-4) . However, inputs are assembled by callers/integrators and `ReceivedOutput` is a publicly deserializable type (`ReceivedOutput::read`), and the wallet `Scanner` is documented to potentially re-emit outputs — a caller that merges scan results across scans/blocks without outpoint dedup will hit this. The missing sanity check is a real footgun in exactly the "compose calls twice" pattern the original report highlights.

### Recommendation

In `SignableTransaction::new`, reject duplicate `OutPoint`s (e.g., insert each `input.outpoint` into a `HashSet` and error with a new `TransactionError::DuplicateInput` variant) before computing `input_sat` and building `tx_ins`. Optionally also reject inputs whose `outpoint`/`prevout` pairs disagree in `script_pubkey` versus the derived offset key earlier, though `multisig` already checks the latter.

### Proof of Concept

```rust
// Conceptual PoC (regtest-style, mirroring networks/bitcoin/tests/wallet.rs)
let output = scanner.scan_transaction(&funding_tx).pop().unwrap();

// Supply the same ReceivedOutput twice — the "called twice in one batch" analog
let inputs = vec![output.clone(), output.clone()];

let payments = [(external_script, output.value().to_sat() + 1000)];

// Double-counted input_sat makes this pass NotEnoughFunds despite the real
// unique input being insufficient to cover the payment.
let tx = SignableTransaction::new(inputs, &payments, None, None, FEE).unwrap();

// The multisig signs a transaction Bitcoin consensus will reject (duplicate TxIn).
let signed = sign(&keys, &tx);
assert_eq!(signed.input[0].previous_output, signed.input[1].previous_output);
// rpc.send_raw_transaction(&signed) -> "bad-txns-inputs-duplicate"
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
```rust
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
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

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
  }
```
