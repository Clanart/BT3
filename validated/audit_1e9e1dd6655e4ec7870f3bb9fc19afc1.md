### Title
`Scanner::scan_transaction` / `ReceivedOutput` accept zero-value and dust outputs to a Serai address, inflating the received-funds list with economically unspendable inputs - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
The analog to "deposit functions accepting zero-value contributions, inflating a list everyone must later iterate" is Serai's Bitcoin scanner: any transaction output paying to a Serai Taproot script is registered as a `ReceivedOutput` with no check on `output.value`. An unprivileged attacker can broadcast transactions creating arbitrarily many dust (or even zero-value) outputs to the Serai key, inflating the wallet's output set with entries that are reported as received funds yet cost more in fees/signing work to spend than they are worth.

### Finding Description
`Scanner::scan_transaction` iterates a transaction's outputs and records every output whose `script_pubkey` matches a registered script, pushing a `ReceivedOutput` without inspecting `output.value` [1](#0-0) . `scan_block` does the same for every transaction in a block, including the coinbase [2](#0-1) . `ReceivedOutput::read` likewise deserializes any `TxOut`/`OutPoint` without a value check [3](#0-2) , and `value()` just returns the raw satoshi amount [4](#0-3) .

While `send.rs` defines `DUST = 546` and enforces a `DustPayment` minimum on *payments*, no equivalent minimum exists on the receive side — the scanner path never applies it to incoming `TxOut`s [5](#0-4) . Each recorded output later becomes a transaction input that adds ~68 vbytes of weight plus a per-input FROST nonce/preprocess commitment (each input's commitment is re-keyed by `register_offset`/`ReceivedOutput.offset`), so every spammed output costs the set real satoshis and real signing bandwidth to consolidate.

### Impact Explanation
- **Funds reported received that are not spendable**: the processor treats every `ReceivedOutput` as balance (`balance()` in `processor/src/networks/bitcoin.rs` returns `output.value()`), yet a 546-sat (or smaller) input contributes less than the marginal fee of spending it, so it can never be economically spent — it is dead weight the protocol must either burn fees on or leave unspendable forever.
- **Inflated cost of every plan**: like the OpenQ deposits array, the output set grows for free (attacker pays only dust + one-time tx fee, or near-nothing with batched outputs), while every downstream consumer — input selection, weight calculation `calculate_weight_vbytes`, and the multisig signing protocol — pays per element.

### Likelihood Explanation
Reachable by any unprivileged party who can send a Bitcoin transaction to a publicly known Serai Taproot address — exactly the "Bitcoin transactions they send" reachability class. Costs are minimal (a single transaction can create dozens of dust outputs to the same script). Mitigating factor: sub-546-sat outputs are nonstandard on mainnet, so the practical spam unit is 546 sats, and full-node mempool policy may slow (not prevent) large-scale spam.

### Recommendation
Apply a minimum-value check in `Scanner::scan_transaction` (e.g., skip `output.value` below `DUST`, or a configurable economic threshold ≥ the marginal spend cost) before pushing a `ReceivedOutput`, and mirror the check in `ReceivedOutput::read` so deserialized state cannot reintroduce zero/dust entries.

### Proof of Concept
```rust
use bitcoin::{Transaction, TxOut, Amount, absolute::LockTime, transaction::Version, ScriptBuf};
use k256::ProjectivePoint;
use bitcoin_serai::wallet::{Scanner, p2tr_script_buf};

let key = ProjectivePoint::GENERATOR; // any scannable Serai key
let scanner = Scanner::new(key).unwrap();
let script = p2tr_script_buf(key).unwrap();

// Attacker tx: 100 dust outputs to the Serai address
let tx = Transaction {
  version: Version(2),
  lock_time: LockTime::ZERO,
  input: vec![/* any real input */],
  output: (0..100)
    .map(|_| TxOut { value: Amount::from_sat(546), script_pubkey: script.clone() })
    .collect(),
};

let received = scanner.scan_transaction(&tx);
assert_eq!(received.len(), 100);          // all registered as funds
assert!(received.iter().all(|o| o.value() == 546)); // each worth less than spend cost
```
No filtering occurs; all 100 dust outputs are accepted as spendable balance even though spending each costs more in fees than 546 sats.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L116-118)
```rust
  pub fn value(&self) -> u64 {
    self.output.value.to_sat()
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L122-134)
```rust
  pub fn read<R: Read>(r: &mut R) -> io::Result<ReceivedOutput> {
    let offset = Secp256k1::read_F(r)?;
    let output;
    let outpoint;
    {
      let mut buf_r = BufReader::with_capacity(0, r);
      output =
        TxOut::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid TxOut"))?;
      outpoint =
        OutPoint::consensus_decode(&mut buf_r).map_err(|_| io::Error::other("invalid OutPoint"))?;
    }
    Ok(ReceivedOutput { offset, output, outpoint })
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

**File:** networks/bitcoin/src/wallet/send.rs (L32-41)
```rust
pub const DUST: u64 = 546;

#[derive(Clone, PartialEq, Eq, Debug, Error)]
pub enum TransactionError {
  #[error("no inputs were specified")]
  NoInputs,
  #[error("no outputs were created")]
  NoOutputs,
  #[error("a specified payment's amount was less than bitcoin's required minimum")]
  DustPayment,
```
