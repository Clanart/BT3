### Title
Coinbase outputs reported as immediately spendable, causing consensus-invalid spends — (`networks/bitcoin/src/wallet/mod.rs`)

### Summary
`Scanner::scan_block` scans every transaction in a block, including the coinbase (`block.txdata[0]`), and returns matching outputs as `ReceivedOutput`s that are indistinguishable from ordinary spendable outputs. Bitcoin consensus rules forbid spending coinbase outputs until they are 100 blocks deep. `ReceivedOutput` carries no maturity/confirmation metadata, and neither `SignableTransaction::new` nor `TransactionSignMachine::sign` performs any maturity check, so the FROST signing pipeline will happily produce a fully-signed transaction that the Bitcoin network rejects as non-final.

### Finding Description
The external report describes missing chain/context validation allowing replay across networks. The analogous missing contextual validation in Serai's in-scope Bitcoin code is the absence of coinbase-maturity validation in the scan → spend pipeline:

- `scan_block` iterates `block.txdata` unconditionally, explicitly including `txdata[0]` (the coinbase), and only notes in a comment that "a post-processing pass is needed" if outputs must be immediately spendable [1](#0-0) .
- `scan_transaction` matches purely on `output.script_pubkey` against the registered script map and emits a `ReceivedOutput { offset, output, outpoint }` with no flag recording that the parent transaction was a coinbase [2](#0-1) .
- `ReceivedOutput` itself stores only `offset`, `output`, and `outpoint` — there is no field for coinbase status, block height, or confirmations, and `ReceivedOutput::read`/`write` serialize none either [3](#0-2) .
- `SignableTransaction::new` consumes `Vec<ReceivedOutput>` and builds inputs directly from `input.outpoint`, checking only dust, fee, funds sufficiency, and weight — never maturity [4](#0-3) .
- `TransactionSignMachine::sign` then computes `taproot_key_spend_signature_hash` over `Prevouts::All` and produces valid BIP-340 shares for a transaction Bitcoin consensus will reject [5](#0-4) .

Because a coinbase spends freshly minted coins, any miner can direct a coinbase output to Serai's P2TR script. Within the 100-block maturity window that output is reported by `Scanner` as received funds, but no valid transaction can spend it.

### Impact Explanation
This satisfies "funds reported received that are not spendable." An accounting layer built on `scan_block` credits a balance that cannot be moved; if the coordinator/processor aggregates such outputs into a `SignableTransaction`, the whole threshold-signing round produces a transaction guaranteed to fail mempool acceptance and block inclusion — burning the signing session and, depending on batching logic, stalling legitimate payments bundled in the same transaction. Additionally, reorgs that replace a coinbase invalidate the outpoint entirely, yet the `ReceivedOutput` still looks spendable to `SignableTransaction`.

### Likelihood Explanation
Any miner (or pool) can include an output paying the Serai P2TR script in a coinbase they produce — this requires no interaction with Serai and is a normal, consensus-valid transaction. Reachability is bounded by the attacker's ability to win a block, which is expensive on mainnet but trivial on lower-difficulty environments and entirely possible for any existing miner. The bug triggers deterministically once such an output exists, because `scan_block` treats the coinbase identically to any other transaction. Likelihood of the condition arising is moderate; impact is bounded to accounting inaccuracy and wasted/failed signing rounds rather than theft, meriting Medium.

### Recommendation
Record maturity context in `ReceivedOutput` (e.g., an `is_coinbase: bool` and/or block height), set it in `scan_block`/`scan_transaction` when the scanned `Transaction::is_coinbase()` is true, and either:

- have `scan_block` skip `block.txdata[0]` by default and offer an explicit `scan_coinbase` path, or
- have `SignableTransaction::new` reject inputs flagged as immature coinbase outputs.

Consumers tracking confirmations should additionally gate inputs on ≥100 confirmations for coinbase-derived outpoints.

### Proof of Concept
1. A miner mines a block whose coinbase (`txdata[0]`) contains `TxOut { value: 1_000_000, script_pubkey: <serai_p2tr_script> }`.
2. `Scanner::scan_block(&block)` returns `ReceivedOutput { offset: Scalar::ZERO (or registered offset), output, outpoint: (coinbase_txid, vout) }`, indistinguishable from a normal deposit [6](#0-5) .
3. The caller passes it to `SignableTransaction::new(vec![received], payments, change, None, fee_rate)`, which succeeds — no maturity check exists [7](#0-6) .
4. `multisig()`/`TransactionSignMachine::sign` produce valid FROST shares and a complete witness [8](#0-7) .
5. Broadcasting fails: Bitcoin consensus/policy rejects the spend with `bad-txns-premature-spend-of-coinbase` until the coinbase is 100 blocks deep. The wallet reported funds as received and spendable that were not.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L89-97)
```rust
#[derive(Clone, PartialEq, Eq, Debug)]
pub struct ReceivedOutput {
  // The scalar offset to obtain the key usable to spend this output.
  offset: Scalar,
  // The output to spend.
  output: TxOut,
  // The TX ID and vout of the output to spend.
  outpoint: OutPoint,
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

**File:** networks/bitcoin/src/wallet/mod.rs (L216-227)
```rust
  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
  }
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-185)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

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

**File:** networks/bitcoin/src/wallet/send.rs (L373-391)
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
        )?;
```
