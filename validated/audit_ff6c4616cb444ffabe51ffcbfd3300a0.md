### Title
Unbounded accumulation of attacker-created outputs: `Scanner::scan_transaction` has no dust/value filter, so every output to the public multisig script becomes a permanently tracked `ReceivedOutput` - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Xen bug class: handling a repeated, unprivileged request (`XS_RESET_WATCHES`) leaks tracking state, giving an unbounded-memory DoS. In Serai, the analogous "watch" is a registered script in `Scanner`, and the analogous "reset" is each incoming Bitcoin transaction: `Scanner::scan_transaction`/`scan_block` accept every output paying to a registered `script_pubkey` and emit a `ReceivedOutput` with no minimum-value or dust filter [1](#0-0) . Because the multisig script_pubkey is public on-chain data, any unprivileged party can send arbitrarily many tiny outputs to it. Each one becomes a tracked output that the downstream pipeline stores permanently (`seen` DB entries, `ram_outputs`, scheduler inputs) and later turns into a transaction input requiring an individual FROST signing machine per input [2](#0-1) .

### Finding Description
`Scanner::scan_transaction` iterates `tx.output` and pushes a `ReceivedOutput` for every `script_pubkey` present in `self.scripts`, with no threshold on `output.value` [1](#0-0) . The only dust check in the codebase (`output.balance().amount.0 >= N::DUST`) lives in the processor's scanner loop, not in the wallet `Scanner` itself [3](#0-2) , so the filtering is an optional integrator choice rather than a property of the primitive. Every retained output is also recorded with a permanent `seen` marker and a `ram_outputs` entry that is only pruned on acknowledgment, and duplicates panic rather than being dropped [4](#0-3) . Additionally, `SignableTransaction::new` places no cap on the number of inputs beyond the standard transaction weight, so thousands of dust outputs each spawn a `Schnorr` `AlgorithmMachine` and an independent signing round [5](#0-4) [2](#0-1) .

### Impact Explanation
An attacker who knows the multisig's Taproot script_pubkey (public information, derivable from any on-chain spend or the group key) can continuously send dust-valued outputs to it. Each output (a) permanently grows the output-tracking state (`seen` keys, `ram_outputs`, scheduler input lists), mirroring the unfreed watch-tracking in oxenstored, and (b) forces the threshold signing protocol to build one FROST `AlgorithmMachine`/`SignableTransaction` input per output, consuming memory and per-input signing work for UTXOs that may never be economical to spend. This is a persistent, cumulative resource-exhaustion DoS: the tracking state never shrinks even though the outputs have negligible value.

### Likelihood Explanation
Triggering the condition requires only sending standard Bitcoin transactions to a public address — fully within the "Bitcoin transactions they send" reachability rule and needing no key material, validator status, or malformed encodings. The cost to the attacker is the dust amount per output plus fees, so exploitation is cheap-in-kind but not free; severity is Medium (system-wide resource exhaustion without key/signature compromise).

### Recommendation
Move the dust filter into `Scanner::scan_transaction` itself — e.g., skip outputs with `output.value.to_sat() < DUST` (the constant already defined in `wallet/send.rs` [6](#0-5) ) or accept a configurable minimum-value parameter — so non-spendable-value outputs never enter `ReceivedOutput` tracking. Also consider bounding the number of inputs accepted by `SignableTransaction::new` and handling repeated output IDs without a `panic!`.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/mod.rs::Scanner::scan_transaction
// Given the public group key's p2tr script_pubkey, an attacker crafts a
// transaction with N outputs of value 1 sat each to that script:
let tx = Transaction {
  /* ... */
  output: (0 .. 10_000)
    .map(|_| TxOut { value: Amount::from_sat(1), script_pubkey: multisig_script.clone() })
    .collect(),
  /* ... */
};
let received = scanner.scan_transaction(&tx);
// received.len() == 10_000; every 1-sat output is reported as a ReceivedOutput
// and is subsequently stored in `seen`/`ram_outputs` and fed to
// SignableTransaction::new, which creates one FROST signing machine per input
// (send.rs:273-285). Repeating this across blocks grows tracked-output state
// and signing work without bound — the same unbounded-tracking DoS as
// XS_RESET_WATCHES leaking watch state in oxenstored.
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

**File:** processor/src/multisigs/scanner.rs (L562-567)
```rust
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
          }
```

**File:** processor/src/multisigs/scanner.rs (L634-639)
```rust
          let seen = ScannerDb::<N, D>::seen(&db, &id);
          let id = id.as_ref().to_vec();
          if seen || scanner.ram_outputs.contains(&id) {
            panic!("scanned an output multiple times");
          }
          scanner.ram_outputs.insert(id);
```
