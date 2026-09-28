### Title

Quadratic transaction-ID hashing enables scanner denial of service - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary

`Scanner::scan_transaction` recomputes the transaction ID once for every output paying a registered Serai script. A transaction containing many matching outputs therefore causes the full transaction to be hashed once per output, creating quadratic hashing work relative to the number of matching outputs. [1](#0-0) 

### Finding Description

For each transaction output, `scan_transaction` checks `self.scripts` and, when the output matches, calls `tx.compute_txid()` while constructing its `OutPoint`. [1](#0-0) 

`compute_txid` hashes the complete transaction serialization. If the transaction has size `S` and contains `N` matching outputs, the scanner performs `N` separate `O(S)` transaction-hash computations. Because `S` grows with `N`, an attacker-controlled transaction paying many outputs to the scanned script results in approximately `O(N²)` hashing work. [2](#0-1) 

The TXID is invariant for the transaction and does not depend on `vout`, so it only needs to be computed once before iterating over the outputs. [1](#0-0) 

### Impact Explanation

An unprivileged party that can cause a transaction containing numerous outputs to a registered Serai address to be scanned can make the scanner consume disproportionate CPU time. Repeating such transactions can keep the scanning path saturated and delay or prevent subsequent transactions and blocks from being processed. [3](#0-2) 

This is the same bug class as the nghttp2 report: a repeated expensive operation is applied to attacker-controlled repeated elements, rather than computing the invariant result once. [2](#0-1) 

### Likelihood Explanation

The attacker needs to know or obtain a script being scanned, which is feasible because Bitcoin deposit addresses and their output scripts are publicly visible on-chain. `Scanner` directly maps matching output scripts to registered scalar offsets and returns every matching output. [4](#0-3) [5](#0-4) 

The attack requires creating many outputs to that script. Standard relay dust rules may require each output to carry a minimum amount, imposing a real economic cost, but no validator key, private state, malformed encoding, or malicious Serai peer is required. [6](#0-5) 

### Recommendation

Compute `tx.compute_txid()` once before the output loop, or lazily on the first matching output, then reuse the resulting `Txid` for every `OutPoint`. [1](#0-0) 

```rust
let txid = tx.compute_txid();

for (vout, output) in tx.output.iter().enumerate() {
  let Ok(vout) = u32::try_from(vout) else { break };

  if let Some(offset) = self.scripts.get(&output.script_pubkey) {
    res.push(ReceivedOutput {
      offset: *offset,
      output: output.clone(),
      outpoint: OutPoint::new(txid, vout),
    });
  }
}
```

A protocol-level bound on matched outputs can additionally limit work, but caching the TXID removes the quadratic amplification directly. [3](#0-2) 

### Proof of Concept

The following in-crate test demonstrates the excessive recomputation path. Increasing `MATCHING_OUTPUTS` increases both transaction size and the number of full-transaction hash operations:

```rust
#[test]
fn repeated_txid_hashing_for_matching_outputs() {
  use bitcoin::{
    transaction::{Version, Transaction},
    absolute::LockTime,
    Amount, OutPoint, ScriptBuf, Sequence, TxIn, TxOut, Witness,
  };
  use k256::ProjectivePoint;

  use crate::wallet::{p2tr_script_buf, Scanner};

  const MATCHING_OUTPUTS: usize = 10_000;

  let scanner = Scanner::new(ProjectivePoint::GENERATOR).unwrap();
  let scanned_script = p2tr_script_buf(ProjectivePoint::GENERATOR).unwrap();

  let tx = Transaction {
    version: Version(2),
    lock_time: LockTime::ZERO,
    input: vec![TxIn {
      previous_output: OutPoint::null(),
      script_sig: ScriptBuf::new(),
      sequence: Sequence::MAX,
      witness: Witness::new(),
    }],
    output: vec![
      TxOut {
        value: Amount::from_sat(546),
        script_pubkey: scanned_script,
      };
      MATCHING_OUTPUTS
    ],
  };

  // This returns 10,000 outputs and calls Transaction::compute_txid once for
  // each one, hashing the whole transaction 10,000 times.
  let outputs = scanner.scan_transaction(&tx);
  assert_eq!(outputs.len(), MATCHING_OUTPUTS);
}
```

The vulnerable behavior is at `OutPoint::new(tx.compute_txid(), vout)`, which is evaluated inside the per-output match branch rather than once per transaction. [7](#0-6)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L153-165)
```rust
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}

impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
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

**File:** networks/bitcoin/src/wallet/send.rs (L27-33)
```rust
#[rustfmt::skip]
// https://github.com/bitcoin/bitcoin/blob/306ccd4927a2efe325c8d84be1bdb79edeb29b04/src/policy/policy.cpp#L26-L63
// As the above notes, a lower amount may not be considered dust if contained in a SegWit output
// This doesn't bother with delineation due to how marginal these values are, and because it isn't
// worth the complexity to implement differentation
pub const DUST: u64 = 546;

```
