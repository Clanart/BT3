### Title
Attacker Can Grief the Multisig by Flooding It with Dust UTXOs, Forcing Unprofitable/Overweight Input Sets - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` in `networks/bitcoin/src/wallet/mod.rs` accepts any output paying to a registered script, with no minimum-value (dust) check. An unprivileged attacker can send many dust/dust-adjacent P2TR outputs to the multisig's Taproot script, inflating the pool of `ReceivedOutput`s. When `SignableTransaction::new` later consumes them as inputs, each input adds a fixed ~58-vbyte cost; spending a sub-fee-value output is a net loss, and aggregating many of them pushes the transaction past `MAX_STANDARD_TX_WEIGHT`, making the received funds economically unspendable.

### Finding Description
`Scanner` indexes outputs purely by `script_pubkey` match (`scan_transaction`, mod.rs:199-214). There is no `value >= DUST` (or fee-covering) filter on received outputs, mirroring the missing "minimum stake amount" in the reference report — the cost of creating a grief output is borne by the attacker only once, while the cost of spending it is borne by the victim wallet every time. `SignableTransaction::new` (send.rs:150-256) validates *payments* against `DUST` (send.rs:165-169) but performs no equivalent check on *inputs*: it unconditionally builds `tx_ins` from all provided `inputs` (send.rs:177-185), computes `needed_fee = fee_per_vbyte * vbytes` on the resulting weight (send.rs:204-206), and only fails with `TooLargeTransaction` once weight exceeds the standardness limit (send.rs:241-243). Each dust input also forces a distinct per-input signature machine (`multisig`, send.rs:273-285), multiplying coordination cost.

### Impact Explanation
- Every dust UTXO scanned becomes an input candidate whose spend cost (`fee_per_vbyte * ~57.5 vbytes`) at typical fee rates exceeds its value; including it destroys value rather than recovering it.
- An attacker who floods the address with enough dust forces either (a) stranded funds — inputs that can never be profitably spent — or (b) `TooLargeTransaction` failures when the input set grows past `MAX_STANDARD_TX_WEIGHT`, requiring input-set filtering logic that does not exist in this crate.
- This is "funds reported received that are not spendable": the scanner reports outputs as legitimately received while the wallet layer can only spend them at a loss or not at all.

### Likelihood Explanation
The attacker needs only to send ordinary Bitcoin transactions to the multisig's public P2TR address — a fully unprivileged action with no required relationship to Serai. The cost is bounded by the dust outputs' value (~546 sats each), which is cheap griefing on L1. The victim path is deterministic: every received output is recorded by `scan_transaction` without filtering, and `SignableTransaction::new` enforces the weight limit as a hard error, not a selection heuristic.

### Recommendation
Filter received outputs by value: have `Scanner::scan_transaction` (or the output-processing layer) drop outputs below a threshold such as `DUST` or, better, `fee_per_vbyte * input_vbytes` so every accepted output is profitable to spend. Alternatively, enforce a minimum received amount documented alongside `DUST` in `send.rs`, and/or have `SignableTransaction::new` skip sub-threshold inputs rather than erroring.

### Proof of Concept
```rust
// Attacker sends N transactions, each creating a tiny P2TR output to the
// multisig's tweaked key script. No minimum value is enforced on receive:
let mut scanner = Scanner::new(group_key).unwrap();
for tx in &attacker_txs {
    let outs = scanner.scan_transaction(tx); // accepts 1-sat outputs
    all_outputs.extend(outs); // funds "received" per the scanner
}
// Later, spending them:
let stx = SignableTransaction::new(
    all_outputs, &payments, Some(change), None, fee_per_vbyte,
);
// Outcome 1: input_sat is consumed by needed_fee for ~useless inputs.
// Outcome 2: with enough dust inputs, weight > MAX_STANDARD_TX_WEIGHT
//            -> Err(TransactionError::TooLargeTransaction), funds unspendable
//            through this path.
``` [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** networks/bitcoin/src/wallet/send.rs (L165-185)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

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

**File:** networks/bitcoin/src/wallet/send.rs (L204-243)
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

    // If there's a change address, check if there's change to give it
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

    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
