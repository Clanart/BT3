### Title
Self-directed payment is re-scanned as a new external deposit, double-counting funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The reference bug is a missing `to != address(this)`-style guard: when a transfer's destination is the pool itself, the accounting treats the amount as having left the pool while the liquidity is actually retained, so the interest/balance update is computed on the wrong direction. The analog in Serai's in-scope code is the same shape: `SignableTransaction::new` places no restriction on payment destinations, while `Scanner::scan_transaction` reports every transaction output whose `script_pubkey` is in its watched `scripts` map as a freshly `ReceivedOutput`. A spend whose payment output points back at the multisig's own watched script (the external deposit address or any registered offset) is therefore treated simultaneously as funds leaving (a paid payment) and as new funds arriving (a scanned external output), double-counting the balance.

### Finding Description
`Scanner` watches a set of `script_pubkey` values: the base p2tr script for the group key (inserted in `Scanner::new`, `networks/bitcoin/src/wallet/mod.rs:164`) plus one script per registered offset (`register_offset`, lines 185–191). `scan_transaction` matches purely on `output.script_pubkey` membership and returns a `ReceivedOutput` for each match (lines 199–213). There is no notion of transaction provenance — an output the multisig itself created is indistinguishable from an external deposit.

`SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) validates that payments exceed dust, fit weight, and are covered by inputs, but never checks whether a payment's `script_pubkey` collides with the wallet's own scripts. The upstream scheduler (`processor/src/multisigs/scheduler/utxo.rs:318-333`) explicitly drops payments to the multisig's *own branch address* — acknowledging this exact self-payment hazard — but performs no filtering for the external deposit address, the change address, the forward address, or any other offset script the Scanner is watching.

An unprivileged user controls payment destinations through burn/out instructions. By setting the destination script to Serai's own external deposit script (a public value derivable from the known group key via `p2tr_script_buf`), they get a transaction that:
1. Spends real multisig UTXOs and pays a fee.
2. Outputs funds back to a script the Scanner watches.
3. Is re-scanned by `scan_transaction` as a new `OutputType::External` receipt, flowing into the batch/instruction pipeline as a fresh deposit.

### Impact Explanation
The same funds are debited as a payment and credited as a new deposit. The credited "deposit" carries no corresponding burn lock on the Serai side, so downstream accounting mints/credits value out of the multisig's own reserves — the exact misaccounting of the reference report, where liquidity returned to the pool was still accounted as withdrawn. Each repetition only costs transaction fees, and the net value extracted per cycle is the payment amount minus fees and aggregation costs, while the multisig's reserve is double-spent on paper.

### Likelihood Explanation
The trigger is fully within an unprivileged party's reach: choose an external address for a withdrawal equal to the multisig's own watched script. No validator collusion, leaked key, or malicious node is needed — the wallet and scanner code produce the misaccounting from ordinary, syntactically valid transactions. The scheduler's existing special-case filter for branch addresses demonstrates the hazard is real and only partially mitigated.

### Recommendation
Apply the reference recommendation symmetrically: either reject or drop payments whose `script_pubkey` is watched by the scanner (external, change, forward, and all registered offset scripts) at plan/`SignableTransaction` construction — the analog of `require(to != address(this))` — or teach `scan_transaction`/the output-classification layer to distinguish self-created outputs from external receipts (the analog of updating the accounting direction). The general fix belongs in `SignableTransaction::new` and/or the scheduler's payment filter in `processor/src/multisigs/scheduler/utxo.rs`, extended beyond the branch address to every script the `Scanner` matches.

### Proof of Concept
1. Compute `external_script = p2tr_script_buf(group_key)` — this is exactly the script `Scanner::new` inserts at `wallet/mod.rs:164`.
2. Issue a burn/withdrawal instruction with destination `external_script` and amount `A`.
3. `SignableTransaction::new` accepts it (no self-payment check at `send.rs:165-191`); the signed TX spends multisig inputs and creates an output paying `external_script`.
4. On the next block, `scan_transaction` matches that output at `wallet/mod.rs:205` and emits a `ReceivedOutput` classified downstream as `OutputType::External` — a new deposit of `A` that was never externally funded. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L187-191)
```rust
    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();
```

**File:** processor/src/multisigs/scheduler/utxo.rs (L318-333)
```rust
    // Drop payments to our own branch address
    /*
      created_output will be called any time we send to a branch address. If it's called, and it
      wasn't expecting to be called, that's almost certainly an error. The only way to guarantee
      this however is to only have us send to a branch address when creating a branch, hence the
      dropping of pointless payments.

      This is not comprehensive as a payment may still be made to another active multisig's branch
      address, depending on timing. This is safe as the issue only occurs when a multisig sends to
      its *own* branch address, since created_output is called on the signer's Scheduler.
    */
    {
      let branch_address = N::branch_address(self.key).unwrap();
      payments =
        payments.drain(..).filter(|payment| payment.address != branch_address).collect::<Vec<_>>();
    }
```
