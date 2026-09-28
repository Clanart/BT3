### Title
Scanner credits immature coinbase outputs as spendable, producing an unspendable spend — ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
The JUDAO exploit class is "an untrusted transfer credits balance to a shared address, which downstream logic then treats as freely withdrawable, without validating the provenance/spendability of the credited amount." In `bitcoin-serai`, the analogous surface is `Scanner::scan_block`, which scans `block.txdata` in full — including `block.txdata[0]`, the coinbase transaction. A `ReceivedOutput` produced from a coinbase output is indistinguishable from a normal output and is treated as immediately spendable by `SignableTransaction::new` and `TransactionSignMachine::sign`, which commits to it via `Prevouts::All`. A coinbase output is unspendable for 100 blocks per consensus, so the resulting signed transaction will be rejected by the network. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`Scanner::scan_block` iterates `block.txdata` with no special-casing of the coinbase transaction (`networks/bitcoin/src/wallet/mod.rs:221-227`). `scan_transaction` then pushes a `ReceivedOutput` for every output whose `script_pubkey` matches a registered script (`mod.rs:199-214`), recording only `offset`, `output`, and `outpoint` — there is no field or check distinguishing a coinbase output from a regular one (`mod.rs:90-97`, `mod.rs:206-211`).

Downstream, `SignableTransaction::new` sums `input.output.value` into `input_sat` and builds `prevouts` unconditionally (`send.rs:175-185`, `send.rs:253`), and `TransactionSignMachine::sign` produces real FROST Schnorr signature shares committing to all prevouts via `Prevouts::All` (`send.rs:373-391`). Bitcoin consensus rejects any transaction spending a coinbase output before it has 100 confirmations (BIP-30 maturity rule), so the completed transaction is unbroadcastable.

An unprivileged miner — anyone can mine a block — can direct a coinbase output to the multisig's external/branch/change/forward script (the scripts are fixed functions of the group key via `p2tr_script_buf` and the registered hash-to-F offsets in `processor/src/networks/bitcoin.rs:308-346`). The scanner will then report this output as received even though it cannot be spent for 100 blocks.

### Impact Explanation
This satisfies the accepted impact "funds reported received that are not spendable": the scanner emits a `ReceivedOutput` that downstream wallet code treats as a valid input. When such an output is included among `inputs`, `SignableTransaction` signs and `complete` returns a transaction (`send.rs:413-428`) that is consensus-invalid, wasting a full threshold signing round and stalling the spend pipeline until the offending output is excluded. If the immature output is bundled with legitimate inputs, the entire transaction fails, not just the coinbase leg.

### Likelihood Explanation
Requires a miner to construct a coinbase paying the scanner's script — permissionless but requires actually mining a block, so likelihood is low-moderate. Once emitted, triggering is automatic: nothing in `ReceivedOutput`, `SignableTransaction::new`, or the multisig machine rejects coinbase provenance; the only mitigation is an external post-processing filter, which the doc comment suggests but which is not enforced anywhere in the in-scope code (`mod.rs:216-220`).

### Recommendation
In `Scanner::scan_block` (or in `scan_transaction` via a flag), skip `block.txdata[0]` or annotate the `ReceivedOutput` as immature and exclude it until 100 confirmations have elapsed, so credited outputs are always spendable. Alternatively, add a `is_coinbase`/maturity field to `ReceivedOutput` and make `SignableTransaction::new` reject immature inputs.

### Proof of Concept
1. A miner mines a block whose coinbase `txdata[0]` pays `value` sats to `p2tr_script_buf(group_key)` (the external address script).
2. `Scanner::scan_block(&block)` returns `[ReceivedOutput { offset: ZERO, output: <coinbase TxOut>, outpoint: <coinbase txid:0> }]` — verified by reading `mod.rs:221-227` calling `scan_transaction`, which matches `self.scripts` on `script_pubkey` at `mod.rs:205` with no coinbase exclusion.
3. Within 100 blocks, `SignableTransaction::new(vec![coinbase_output], payments, change, None, fee)` succeeds (`send.rs:150-256` performs no maturity check), `multisig()` passes (`send.rs:277` only checks `script_pubkey` against the offset key), and `sign`/`complete` yield a validly-signed but consensus-invalid transaction spending an immature coinbase.
4. Broadcasting fails: the network rejects the spend of an immature coinbase output, demonstrating funds reported received that are not spendable. [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L199-227)
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-397)
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
        shares.push(share);
        Ok(sig)
      })
      .collect::<Result<_, _>>()?;

    Ok((TransactionSignatureMachine { tx: self.tx.tx, sigs }, shares))
```
