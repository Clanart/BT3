### Title
Scanner reports immature coinbase outputs as spendable funds — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The reward-claim bug class — a state transition that fails to enforce a protocol-mandated validity bound (stake expiry / 180-day cap), letting a claim be counted past its valid window — maps onto `Scanner::scan_block`. It scans every transaction in a block, including `block.txdata[0]` (the coinbase), and emits `ReceivedOutput`s that are immediately usable by `SignableTransaction::new`, despite coinbase outputs being unspendable for 100 blocks by Bitcoin consensus. There is no maturity check, no flag on `ReceivedOutput`, and no enforcement anywhere downstream.

### Finding Description
`Scanner::scan_block` iterates `block.txdata` in full and passes each transaction to `scan_transaction`, which matches only `output.script_pubkey` against registered scripts and returns a `ReceivedOutput` containing just `offset`, `output`, and `outpoint` [1](#0-0) . `ReceivedOutput` carries no notion of coinbase/immaturity, and `SignableTransaction::new` consumes `Vec<ReceivedOutput>` and builds spends for each input without any maturity check [2](#0-1) . The only mitigation is a doc comment on `scan_block` noting a "post-processing pass is needed" [3](#0-2)  — analogous to the report's missing expiry check: enforcement is left to the caller rather than the code that creates the invalid state. Downstream, `TransactionSignMachine::sign` produces a real FROST signature over a sighash committing to `Prevouts::All`, so the invalid input is baked into a signed transaction that consensus will reject [4](#0-3) .

### Impact Explanation
Funds reported as received are not spendable. A `ReceivedOutput` derived from an immature coinbase is indistinguishable from a normal UTXO: it has a valid offset, script, and outpoint. Any accounting built on `scan_block` (balances, input selection) overcounts available funds, and any `SignableTransaction` including it produces a fully-signed transaction that fails consensus validation (BIP-34/COINBASE_MATURITY = 100 blocks). For a multisig coordinator this can burn a full FROST signing round and, worse, if the invalid spend is broadcast and rebroadcast attempts are automated, cause repeated signing sessions over transactions that can never confirm — and if combined with fee logic the other inputs' value may be committed to a fee in a doomed transaction.

### Likelihood Explanation
Reachable by an unprivileged party with public inputs: any miner can pay a watcher's registered script in a coinbase output. Mining a block is permissionless (no validator status, no collusion, no key material needed). The triggering condition is automatic — the block containing the coinbase is scanned via the public `scan_block` API, exactly the "untrusted transaction data fed to the library" reachability model. Exploitation cost is real (mining a block), but no protocol privilege is required.

### Recommendation
Enforce the bound where the state is created, mirroring the report's remediation ("restrict claiming after expiry"):
- In `scan_block`, skip `block.txdata[0]` or mark the resulting `ReceivedOutput`s as coinbase (e.g., a `coinbase: bool` field set when `tx.is_coinbase()`), and
- In `SignableTransaction::new`, reject coinbase `ReceivedOutput`s (or accept a block-height/confirmation parameter and check `confirmations >= 100`), so immature outputs cannot be committed to a sighash.

### Proof of Concept
```rust
// A miner includes txid pays to a script registered via Scanner::register_offset
// in the block's coinbase transaction (block.txdata[0]).
let outputs = scanner.scan_block(&block);
// outputs[0] is a ReceivedOutput with a valid offset/outpoint,
// indistinguishable from a mature UTXO.
assert!(!outputs.is_empty());

// SignableTransaction::new accepts it; TransactionSignMachine::sign
// produces a threshold-signed tx committing to Prevouts::All.
let stx = SignableTransaction::new(outputs, &payments, change, None, fee_rate).unwrap();
// The signed transaction is consensus-invalid for 100 blocks:
// spending a coinbase output before maturity.
```

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
