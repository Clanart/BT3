### Title
Quadratic transaction-ID recomputation enables scanner denial of service - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`Scanner::scan_transaction` recomputes the full transaction ID for every output paying a watched script. A transaction containing many outputs to the same Serai-controlled Taproot script therefore causes \(O(n^2)\) work: one full transaction serialization/hash for each of \(n\) matching outputs.

### Finding Description
`scan_transaction` iterates over `tx.output` and checks whether each `script_pubkey` is registered in `self.scripts`. For every matching output, it constructs `ReceivedOutput::outpoint` with `tx.compute_txid()`, repeating the transaction-wide hash calculation inside the loop instead of computing it once. `scan_block` applies this operation to every transaction in a block, so malicious transactions propagate directly into block scanning. [1](#0-0) [2](#0-1) 

### Impact Explanation
An attacker can send a valid Bitcoin transaction containing many outputs to the same watched Serai script. Each output is individually reported, but the scanner hashes the entire transaction once per reported output. A block-sized transaction with tens of thousands of matching outputs can therefore turn a scan that should require one pass over the transaction into tens of thousands of full transaction hashes, potentially exhausting processor time and preventing wallet/block-scan progress.

### Likelihood Explanation
The trigger only requires public transaction data: an output's `script_pubkey` must equal one of the scanner's registered Taproot scripts. Sending many small outputs to one known address is a normal Bitcoin capability, and each output only needs to satisfy the surrounding scanner policy to become relevant. The expensive operation is unconditional after the script match.

### Recommendation
Compute `tx.compute_txid()` once before iterating over `tx.output`, store it in a local variable, and reuse it when constructing every `OutPoint`. This preserves behavior while reducing scanning from quadratic to linear in transaction size.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs
//
// Conceptual trigger:
let script = p2tr_script_buf(watched_even_key).unwrap();
let tx = Transaction {
    version: Version(2),
    lock_time: LockTime::ZERO,
    input: vec![attacker_funded_input],
    output: vec![
        TxOut {
            value: Amount::from_sat(10_000),
            script_pubkey: script.clone(),
        };
        20_000
    ],
};

// Every output matches `scanner.scripts`, so this invokes
// `tx.compute_txid()` 20,000 times over the full transaction.
let received = scanner.scan_transaction(&tx);
```

For \(n\) matching outputs and transaction serialized size \(m = \Theta(n)\), current behavior performs \(n\) hashes of size \(m\), or \(\Theta(nm) = \Theta(n^2)\). Caching the transaction ID before the output loop makes the same scan \(\Theta(n)\).

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

**File:** networks/bitcoin/src/wallet/mod.rs (L221-227)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```
