### Title
Scanner credits dust-valued outputs as spendable received funds, causing fee-burn when spent - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`Scanner::scan_transaction` matches outputs solely on `script_pubkey` and records every matching output as a `ReceivedOutput` spendable input, with no minimum-value check. An unprivileged attacker can send dust outputs (e.g. 1 sat, or any value below the ~57-vbyte cost of a Taproot input at any reasonable fee rate) to a registered Serai address. These outputs are then reported as received balance, yet spending them via `SignableTransaction::new` costs more in fees than their value, burning the wallet's other funds — the same mechanism-flaw class as the MakerDAO zero-bid liquidation, where the system treats economically worthless units as valuable.

### Finding Description
`scan_transaction` iterates `tx.output`, looks up `output.script_pubkey` in `self.scripts`, and unconditionally pushes a `ReceivedOutput` carrying `output.value` [1](#0-0) . There is no filter on `value`. The codebase itself defines `DUST = 546` in `send.rs` as the minimum economical output value [2](#0-1) , but that constant is only enforced on outgoing *payments* and *change* [3](#0-2)  — never on scanned *inputs*. `SignableTransaction::new` then sums `input.output.value` from all provided `ReceivedOutput`s and builds a `TxIn` for each [4](#0-3) . The signing path commits to all prevouts via `Prevouts::All` [5](#0-4) , so any included dust input is genuinely signed and spent.

### Impact Explanation
Each Taproot input adds ~230 weight units (~57 vbytes) to the transaction, as documented in the processor's own weight analysis. A dust output worth less than `fee_per_vbyte * 57` is a liability, not an asset: including it strictly decreases the change returned (or increases the fee). An attacker who repeatedly sends cheap dust outputs to a known Serai deposit address (the script_pubkey is public and derivable from the group key via `p2tr_script_buf`) inflates the wallet's apparent balance while forcing fee burn proportional to the number of dust inputs when those outputs are aggregated. This is "funds reported received that are not economically spendable" — the reported balance cannot be realized without a net loss.

### Likelihood Explanation
Requires only that the attacker send a standard Bitcoin transaction to a public address — no key material, no validator collusion, no privileged access. Cost is bounded only by the dust amounts plus the attacker's own fees. Exploitation of the loss requires the wallet's aggregation logic to include the dust inputs, which `SignableTransaction::new` does unconditionally for any `ReceivedOutput` it is given.

### Recommendation
Filter scanned outputs in `Scanner::scan_transaction` (or at `ReceivedOutput` ingestion) against the `DUST` threshold — skip outputs whose `value` is below the cost of spending them — and/or filter dust `ReceivedOutput`s from the `inputs` list in `SignableTransaction::new`.

### Proof of Concept
```rust
// Attacker knows the Serai group key's script_pubkey (public).
// They broadcast a tx paying 1 sat to it.
let dust_tx = Transaction {
  output: vec![TxOut {
    value: Amount::from_sat(1),
    script_pubkey: p2tr_script_buf(group_key).unwrap(),
  }],
  ..
};

// scan_transaction credits it as a ReceivedOutput worth 1 sat
let outputs = scanner.scan_transaction(&dust_tx);
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].value(), 1); // no dust check rejects it

// SignableTransaction::new happily includes it as an input,
// adding ~57 vbytes of fee obligation to spend 1 sat,
// reducing the change output / increasing the effective fee.
let tx = SignableTransaction::new(
  vec![honest_output, outputs[0].clone()],
  &payments,
  Some(change_addr),
  None,
  fee_per_vbyte,
).unwrap();
// tx.fee() now includes ~57*fee_per_vbyte sats spent to claim 1 sat.
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L32-32)
```rust
pub const DUST: u64 = 546;
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-386)
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
```
