### Title
Attacker-controlled dust deposits are reported as spendable `ReceivedOutput`s, enabling fee-burning and `NotEnoughFunds` DoS when spent - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` accepts any transaction output paying a registered `script_pubkey` with **no minimum-value check**, returning it as a `ReceivedOutput` — a type documented as "A spendable output" [1](#0-0) . Any unprivileged party can send dust (or even zero-value) outputs to a scanned address; these are reported as received funds, yet spending them via `SignableTransaction::new` adds ~57 vbytes of weight per input while contributing fewer satoshis than the fee they incur [2](#0-1) .

### Finding Description
In `scan_transaction`, the only filter is `self.scripts.get(&output.script_pubkey)` — the output's `value` is never inspected [3](#0-2) . The downstream construction path enforces `DUST` (546 sats) only on outgoing **payments**, never on **inputs** [4](#0-3) . Each input adds a fixed ~57 vbytes to the transaction weight (per the weight model in `calculate_weight_vbytes` and the commentary giving 57 vbytes/input), so `needed_fee = fee_per_vbyte * vbytes` grows by ~`57 * fee_per_vbyte` per dust input while `input_sat` grows by < 546 [5](#0-4) . For any `fee_per_vbyte` above ~9, a dust input is net-negative: including it *reduces* the funds available for payments/change. This mirrors the LeXscrow bug class — attacker-supplied small deposits corrupt the balance check (`input_sat < payment_sat + needed_fee` → `NotEnoughFunds`) and cause transaction construction to revert, or silently convert the difference into miner fees.

### Impact Explanation
- **Funds reported received that are not spendable**: outputs below the relay dust limit (or even `value == 0`, since `TxOut` consensus decoding imposes no minimum) are returned by `scan_transaction`/`scan_block` as spendable `ReceivedOutput`s; spending them costs more in fees than they are worth, and they cannot be relayed onward as payments (`DustPayment`).
- **DoS / fee burning**: a wallet that consumes scanner results and attempts to spend its full UTXO set will either hit `NotEnoughFunds`/`TooLargeTransaction` (each dust input consumes ~57 of the 400,000 WU budget, so ~520 dust outputs exhaust `MAX_STANDARD_TX_WEIGHT` [6](#0-5) ) or burn the deficit into fees. `SignableTransaction::multisig` will happily sign these transactions since the dust outputs' `script_pubkey` legitimately matches the offset key [7](#0-6) .

### Likelihood Explanation
Sending dust to a Taproot address is permissionless and cheap (an attacker pays only the dust value plus fees). Any integrator that scans outputs for a known key — the documented purpose of `Scanner` — automatically ingests the attacker's outputs. The vulnerability requires no malformed input, no collusion, and no special privileges; only a standard Bitcoin transaction to a publicly known address.

### Recommendation
Enforce a minimum value on scanned outputs and/or on inputs. Concretely: in `scan_transaction`, skip outputs with `output.value.to_sat() < DUST` (or a configurable threshold reflecting the spend cost at the target feerate), and in `SignableTransaction::new` reject inputs whose value is below the marginal fee they add (`fee_per_vbyte * 57`). This makes the scanner's "spendable output" contract accurate regardless of what an attacker deposits.

### Proof of Concept
```rust
// Attacker sends a 1-sat P2TR output to the victim's scanned key.
let mut scanner = Scanner::new(victim_key).unwrap();
let dust_tx = Transaction {
  // ... single output: TxOut { value: Amount::from_sat(1), script_pubkey: p2tr_script_buf(victim_key) }
};

// Scanner reports it as a spendable ReceivedOutput
let outputs = scanner.scan_transaction(&dust_tx);
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].value(), 1); // unspendable dust reported as funds

// Wallet attempts to spend scanned UTXOs at a normal feerate (e.g. 20 sat/vB)
let inputs = vec![legit_output, outputs[0].clone()]; // legit + attacker dust
// Each input adds ~57 vB -> needed_fee grows by ~1140 sats while input_sat grows by 1 sat.
// Either:
//   SignableTransaction::new(inputs, &payments, change, None, 20)
//     -> Err(TransactionError::NotEnoughFunds { .. })   // DoS: deposit flow reverts
// or, if it still passes, the dust input's cost is silently burned as miner fee.
// With ~520 attacker dust outputs:
//   -> Err(TransactionError::TooLargeTransaction)       // weight budget exhausted
```

The root cause is the missing value floor in `scan_transaction` [8](#0-7)  combined with the absence of any input-side dust check in `SignableTransaction::new` [9](#0-8) .

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L88-97)
```rust
/// A spendable output.
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
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

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L204-221)
```rust
    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
    // Technically, if there isn't change, this TX may still pay enough of a fee to pass the
    // minimum fee. Such edge cases aren't worth programming when they go against intent, as the
    // specified fee rate is too low to be valid
    // bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE is in sats/kilo-vbyte
    if needed_fee < ((u64::from(bitcoin::policy::DEFAULT_MIN_RELAY_TX_FEE) * vbytes) / 1000) {
      Err(TransactionError::TooLowFee)?;
    }

    if input_sat < (payment_sat + needed_fee) {
      Err(TransactionError::NotEnoughFunds {
        inputs: input_sat,
        payments: payment_sat,
        fee: needed_fee,
      })?;
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
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
