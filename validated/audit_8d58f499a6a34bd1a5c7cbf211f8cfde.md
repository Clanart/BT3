### Title
Attacker-inflated input set: `Scanner` accepts and reports uneconomical dust outputs whose mandatory aggregation burns fees - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`Scanner::scan_transaction` reports every output paying to a registered Serai script as a `ReceivedOutput`, with no lower bound on value. `SignableTransaction::new` treats every supplied `ReceivedOutput` as a mandatory input whose per-input weight inflates `needed_fee`. An unprivileged party can send dust-valued outputs to any known Serai multisig address, stuffing the "queue" of outputs the wallet must aggregate and forcing a fee cost that exceeds the value received — the same shape as dead `rolloverQueue` entries inflating relayer cost without compensation.

### Finding Description
`scan_transaction` pushes a `ReceivedOutput` for any output whose `script_pubkey` is in `self.scripts`, unconditionally on `output.value`: [1](#0-0) . Anyone who observes the multisig's P2TR key/script (public on-chain once used, or derivable from the group key) can send a 546-sat output to it. The scanner can never reject it — it only matches `script_pubkey` — so the output is permanently registered as a received balance.

On the spend side, every `ReceivedOutput` passed to `SignableTransaction::new` becomes a mandatory `TxIn`: [2](#0-1) . The weight/vbytes — and therefore `needed_fee` — grows linearly in `inputs` because the fee model builds a full transaction with one fixed-size input per entry: [3](#0-2) . The fee is charged against the pooled input sum (`NotEnoughFunds` only fires if inputs can't cover payments + fee), so each dust input adds a spend cost of ~one P2TR input weight while contributing almost nothing. The multisig's own reference constant prices each aggregation input at ~800 sat while the network dust floor is 10,000 sat but bitcoin's is 546: [4](#0-3)  — outputs between 546 and the economic aggregation threshold are still reported by `Scanner` itself.

The only mitigation — filtering outputs below `N::DUST` — lives in the out-of-scope processor shim and is a caller-side convention, not a wallet invariant: [5](#0-4) . Additionally, since `ram_outputs`/seen dedup is by outpoint, each dust outpoint is stored forever; the attacker can mint arbitrarily many per transaction.

### Impact Explanation
Persistent fee-griefing and queue inflation: an attacker converts a small burn (546+ sat per outpoint) into permanent tracked state and forced per-input spending weight for the multisig. With enough dust outpoints the input set either saturates `MAX_STANDARD_TX_WEIGHT` (causing `TooLargeTransaction` and stalling batch spends that consume all received outputs) or raises `needed_fee` to burn real funds on every consolidation: [6](#0-5) . Unlike honest deposits, these "lost positions" are never economical to delist.

### Likelihood Explanation
Reachable by any unprivileged party with a Bitcoin wallet: `scan_transaction`/`scan_block` accept raw on-chain transactions, and the only gate is `script_pubkey` membership — the attacker trivially satisfies it by paying to the known address. Cost scales linearly for the attacker (one output each) but the burden is permanent; re-orgs don't clear `seen`/dedup state.

### Recommendation
Enforce an economic floor inside `bitcoin-serai` rather than relying on caller conventions: skip (or flag) outputs below a parameterized dust threshold in `Scanner::scan_transaction`, and/or have `SignableTransaction::new` drop inputs whose value is less than their marginal fee contribution (`fee_per_vbyte * per_input_vbytes`). Return skipped inputs so callers can still consolidate them deliberately when fee rates are low.

### Proof of Concept
1. Obtain the multisig's external address (`p2tr_script_buf(key)`, the `Scalar::ZERO` script registered in `Scanner::new`: [7](#0-6) ).
2. Broadcast a transaction with N outputs of 546 sat each to that script.
3. After confirmation, `scanner.scan_block` returns N `ReceivedOutput`s — indistinguishable from honest deposits.
4. Any `SignableTransaction::new` consuming them adds ~230 weight units × N to the fee (or fails `TooLargeTransaction`), while the N outputs contribute only 546·N sat — a net loss per input whenever `fee_per_vbyte` exceeds ~2.4 sat/vbyte.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L162-165)
```rust
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

**File:** networks/bitcoin/src/wallet/send.rs (L71-99)
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
    }
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-156)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
```

**File:** networks/bitcoin/src/wallet/send.rs (L237-243)
```rust
    if tx_outs.is_empty() {
      Err(TransactionError::NoOutputs)?;
    }

    if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
      Err(TransactionError::TooLargeTransaction)?;
    }
```

**File:** processor/src/networks/bitcoin.rs (L638-646)
```rust
  const DUST: u64 = 10_000;

  // 2 inputs should be 2 * 230 = 460 weight units
  // The output should be ~36 bytes, or 144 weight units
  // The overhead should be ~20 bytes at most, or 80 weight units
  // 684 weight units, 171 vbytes, round up to 200
  // 200 vbytes at 1 sat/weight (our current minimum fee, 4 sat/vbyte) = 800 sat fee for the
  // aggregation TX
  const COST_TO_AGGREGATE: u64 = 800;
```

**File:** processor/src/multisigs/scanner.rs (L594-640)
```rust
        for output in &outputs {
          let id = output.id();
          info!(
            "block {} had output {} worth {:?}",
            hex::encode(&block_id),
            hex::encode(&id),
            output.balance(),
          );

          // On Bitcoin, the output ID should be unique for a given chain
          // On Monero, it's trivial to make an output sharing an ID with another
          // We should only scan outputs with valid IDs however, which will be unique

          /*
            The safety of this code must satisfy the following conditions:
            1) seen is not set for the first occurrence
            2) seen is set for any future occurrence

            seen is only written to after this code completes. Accordingly, it cannot be set
            before the first occurrence UNLESSS it's set, yet the last scanned block isn't.
            They are both written in the same database transaction, preventing this.

            As for future occurrences, the RAM entry ensures they're handled properly even if
            the database has yet to be set.

            On reboot, which will clear the RAM, if seen wasn't set, neither was latest scanned
            block. Accordingly, this will scan from some prior block, re-populating the RAM.

            If seen was set, then this will be successfully read.

            There's also no concern ram_outputs was pruned, yet seen wasn't set, as pruning
            from ram_outputs will acquire a write lock (preventing this code from acquiring
            its own write lock and running), and during its holding of the write lock, it
            commits the transaction setting seen and the latest scanned block.

            This last case isn't true. Committing seen/latest_scanned_block happens after
            relinquishing the write lock.

            TODO2: Only update ram_outputs after committing the TXN in question.
          */
          let seen = ScannerDb::<N, D>::seen(&db, &id);
          let id = id.as_ref().to_vec();
          if seen || scanner.ram_outputs.contains(&id) {
            panic!("scanned an output multiple times");
          }
          scanner.ram_outputs.insert(id);
        }
```
