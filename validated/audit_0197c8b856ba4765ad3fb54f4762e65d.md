### Title
Scanner credits attacker-created dust outputs which are silently burned as fees when spent - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external LiFi bug is about *latent value being silently consumed*: leftover balance in the contract was absorbed into a swap without being charged to the caller. The Serai analog lives in `Scanner::scan_transaction` / `SignableTransaction::new`: the wallet credits any transaction output whose `script_pubkey` matches a registered script — with no lower bound on `value` — and later `SignableTransaction::new` consumes every credited `ReceivedOutput` as an input, where each input adds ~57 vbytes of weight whose marginal fee is silently burned out of the multisig's funds. An unprivileged attacker can send arbitrarily small (dust/sub-economical) outputs to a Serai Taproot address; these are "received" funds that cost more to spend than they are worth, and their cost is absorbed into the transaction fee rather than rejected.

### Finding Description
`Scanner::scan_transaction` only checks `self.scripts.get(&output.script_pubkey)` and records the output verbatim as a `ReceivedOutput` — there is no `value >= DUST` (or any economic-spendability) check ( [1](#0-0) ). `SignableTransaction::new` then takes `inputs: Vec<ReceivedOutput>` and sums `input_sat` from them while computing `needed_fee = fee_per_vbyte * vbytes`, where each input contributes 230 weight units (~58 vbytes) per the comment in `calculate_weight_vbytes` ( [2](#0-1) ). The only check is the *aggregate* `input_sat < payment_sat + needed_fee` ( [3](#0-2) ), so an input whose value is below its marginal fee cost (e.g. a 546-sat output, the minimum standard P2TR output, versus ~2900 sats of fee at 50 sat/vb) is never rejected. `fee()` confirms the burned amount: `sum(prevouts) - sum(outputs)` ( [4](#0-3) ). Because `TransactionSignMachine::sign` commits to `Prevouts::All` including every input's committed value, the worthless inputs are permanently bound into the signed transaction ( [5](#0-4) ).

### Impact Explanation
Any Bitcoin user can send dust-valued outputs to the multisig's Taproot address (or any registered offset script). These outputs are reported as received funds, and when a scheduler/wallet sweeps "all received outputs" into a `SignableTransaction`, each poisoned input destroys `(marginal_input_vbytes × fee_per_vbyte) − input_value` satoshis of vault funds as excess miner fee — griefing at roughly a 5–9× amplification (attacker spends ~330–546 sats to burn ~2900+ sats at moderate fee rates). This is the same shape as M-02: unaccounted-for "latent" value (here negative-value inputs) is absorbed into the protocol's balance sheet rather than being explicitly charged or rejected.

### Likelihood Explanation
Triggering requires only that an attacker broadcast a standard transaction paying a small output to the Serai address — fully public, unprivileged, and cheap. Exploitation additionally requires the input-selection path to include the dust output; any "spend all scanned outputs" or FIFO input selection does so automatically. There is no dust floor anywhere in `ReceivedOutput`, `Scanner`, or `SignableTransaction::new` to stop it (the `DUST`/`Bitcoin::DUST = 10_000` constants are only applied to *payments/change*, never to *inputs*).

### Recommendation
Enforce an economic-spendability floor on credited inputs: in `Scanner::scan_transaction` (or in `SignableTransaction::new` per-input), drop or error on any output whose `value` is below `DUST` — or better, below `INPUT_VBYTES × fee_per_vbyte` so inputs are only accepted if they are net-positive at the target fee rate. Inputs below the floor should be left unspent (and untracked as spendable balance) rather than silently converting their fee burden into vault losses.

### Proof of Concept
```rust
// Attacker creates a standard TX paying a minimal P2TR output to the Serai key.
// Scanner::scan_transaction credits it unconditionally (mod.rs:205-211):
//   if let Some(offset) = self.scripts.get(&output.script_pubkey) { ... }
let dust_output: ReceivedOutput = scanner.scan_transaction(&attacker_tx).pop().unwrap();
assert!(dust_output.value() < 546); // e.g. 330 sats, standard minimum for P2TR

// Wallet builds a spend using all scanned outputs (send.rs:150-256).
let payments = [(p2tr_script_buf(dest).unwrap(), 100_000)];
let tx = SignableTransaction::new(
  vec![real_input.clone(), dust_output], // dust input sneaks in
  &payments,
  None,
  None,
  50, // sat/vbyte
).unwrap();

// Each input costs ~58 vbytes => dust input adds ~2900 sats of needed_fee
// while contributing only 330 sats. The ~2570 sat difference is burned:
// fee() = sum(prevouts) - sum(outputs) (send.rs:138-141), and
// Prevouts::All commits the dust value into the BIP-341 sighash (send.rs:373-390).
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

**File:** networks/bitcoin/src/wallet/send.rs (L71-82)
```rust
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
```

**File:** networks/bitcoin/src/wallet/send.rs (L138-141)
```rust
  pub fn fee(&self) -> u64 {
    self.prevouts.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>() -
      self.tx.output.iter().map(|prevout| prevout.value.to_sat()).sum::<u64>()
  }
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
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
```
