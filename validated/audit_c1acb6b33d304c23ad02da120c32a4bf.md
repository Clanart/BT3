### Title
Dust-value outputs are registered as spendable inputs, enabling fee drain and unspendable-funds DoS - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes a missing lower-bound enforcement at registration time: because `minimumAssets` is not required when a pool is registered, an attacker can create arbitrarily cheap positions, which both spams the queue and dilutes the per-user fee (`estimatedFee / totalUsers`).

The same bug class exists in `bitcoin-serai`: the `Scanner` registers every output paying to a tracked `script_pubkey` as a `ReceivedOutput` with no minimum-value check, and `SignableTransaction::new` enforces `DUST` on *payments* but never on *inputs*. Any unprivileged party can send dust outputs (e.g., 1 sat, or even sub-dust values up to just under 546 sats) to the multisig's Taproot address. Each such "received" input is economically negative — a P2TR key-spend input costs ~57.5 vbytes, so at any reasonable fee rate spending it costs far more than it contributes — and enough dust inputs push the transaction over `MAX_STANDARD_TX_WEIGHT`, making the wallet's funds unspendable.

### Finding Description
`Scanner::scan_transaction` accepts an output as soon as `self.scripts.get(&output.script_pubkey)` matches, copying the value verbatim into `ReceivedOutput` with no `value` filter. [1](#0-0) 

`SignableTransaction::new` validates `*amount < DUST` only for the *payments* (outputs being created), and sums `input_sat` from whatever `ReceivedOutput`s were passed in, with no per-input minimum. [2](#0-1) [3](#0-2) 

From `calculate_weight_vbytes`, each input is a fixed-size Taproot input with a 64-byte witness (~57.5 vbytes). A 1-sat input therefore contributes ~`57.5 * fee_per_vbyte` sats of required fee while adding only 1 sat — a net loss paid out of the wallet's other inputs. [4](#0-3) 

### Impact Explanation
- **Fee drain**: each dust input included in a spend transaction burns more fee than its value, leaking the validator set's BTC to miners.
- **Unspendable funds (DoS)**: the transaction is rejected once `weight > MAX_STANDARD_TX_WEIGHT` (~400,000 WU). ~1,700 dust inputs reach that bound; if coin selection aggregates registered outputs, the wallet cannot construct a valid spend, and there is no API to exclude dust inputs — `scan_transaction`/`scan_block` return all of them. [5](#0-4) 

### Likelihood Explanation
Reachable by any unprivileged party: sending dust outputs to a known Serai multisig address requires only a normal Bitcoin transaction the attacker pays for once (a single tx can create thousands of dust outputs cheaply at low feerates). No validator collusion, leaked keys, or malformed encodings are needed — the attacker only needs to cause bytes the `Scanner` will match, which is purely `script_pubkey` equality. The cost to create a blocking set of ~1,700 dust UTXOs is ~1,700 sats of value plus one transaction's fee.

### Recommendation
Filter dust at receipt and/or at spend construction:
- In `Scanner::scan_transaction`, skip outputs with `value.to_sat() < DUST` (or a configurable minimum reflecting the current fee rate), so uneconomical outputs are never registered.
- In `SignableTransaction::new`, either reject `ReceivedOutput`s with `value < DUST` or require `input.output.value` to cover its marginal fee (`~57.5 * fee_per_vbyte`) before including it, mirroring the existing payment-side `DustPayment` check.

### Proof of Concept
```rust
// Attacker sends a Bitcoin tx creating N dust outputs to the multisig's
// p2tr script_pubkey. Scanner::scan_transaction registers all of them:
let outputs = scanner.scan_transaction(&attacker_tx);
assert_eq!(outputs.len(), 2000); // all 1-sat outputs registered, no filtering

// Building a spend with them:
let res = SignableTransaction::new(
    outputs,                       // 2000 x 1-sat inputs
    &[(payment_script, 100_000)],  // a legitimate payment
    Some(change_script),
    None,
    10,                            // fee_per_vbyte
);
// Each input adds ~57.5 vbytes => weight exceeds MAX_STANDARD_TX_WEIGHT
// => Err(TransactionError::TooLargeTransaction). The wallet cannot spend.
// Even below the limit, input_sat (2000 sats) < fee consumed by the inputs
// (~115,000 sats at 10 sat/vb), draining other inputs or erroring
// NotEnoughFunds despite the wallet holding funds.
```
Relevant code: `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:199`), `SignableTransaction::new` input/dust handling (`networks/bitcoin/src/wallet/send.rs:165-176,241-243`).

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

**File:** networks/bitcoin/src/wallet/send.rs (L68-98)
```rust
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
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
        inputs
      ],
      output: payments
        .iter()
        // The payment is a fixed size so we don't have to use it here
        // The script pub key is not of a fixed size and does have to be used here
        .map(|payment| TxOut {
          value: Amount::from_sat(payment.1),
          script_pubkey: payment.0.clone(),
        })
        .collect(),
    };
    if let Some(change) = change {
      // Use a 0 value since we're currently unsure what the change amount will be, and since
      // the value is fixed size (so any value could be used here)
      tx.output.push(TxOut { value: Amount::ZERO, script_pubkey: change.clone() });
```

**File:** networks/bitcoin/src/wallet/send.rs (L165-169)
```rust
    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L175-176)
```rust
    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
```

**File:** networks/bitcoin/src/wallet/send.rs (L241-243)
```rust
    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```
