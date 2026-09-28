### Title
Immature coinbase outputs are reported as spendable received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` scans every transaction in `block.txdata`, including `block.txdata[0]` coinbase transactions, and converts matching outputs into `ReceivedOutput` values without tracking confirmations or coinbase maturity. [1](#0-0)  A miner or other party supplying block data can therefore cause an output to be reported as received before it is legally spendable under Bitcoin's 100-block coinbase maturity rule. [2](#0-1) 

### Finding Description
`scan_transaction` treats any transaction output whose `script_pubkey` appears in `self.scripts` as a received output and records its transaction ID and vout. [3](#0-2)  `scan_block` then applies that logic to every transaction in a block without excluding the coinbase or marking its outputs as immature. [4](#0-3) 

The code acknowledges the hazard by stating that coinbase outputs are bound by maturity and require post-processing if immediate spendability is needed, but the returned `ReceivedOutput` contains no maturity state and `SignableTransaction::new` accepts the listed outpoint/value pair as an input. [5](#0-4) [6](#0-5) 

### Impact Explanation
A deposit accounting or wallet layer using `Scanner::scan_block` can report or attempt to spend funds that Bitcoin consensus will reject until the coinbase has matured. [1](#0-0)  If the immature credit is accepted for accounting, a later reorganization or an attempted spend can cause the apparently received funds to disappear or produce an invalid transaction. [7](#0-6) 

### Likelihood Explanation
Any valid block supplied to `scan_block` can contain a miner-created coinbase paying a scanner-registered Taproot script, and the scanner will include it unconditionally. [4](#0-3)  Exploitation requires the caller to treat newly scanned block outputs as immediately spendable deposits rather than applying the documented post-processing pass. [5](#0-4) 

### Recommendation
Change `Scanner::scan_block` to skip `block.txdata.first()` or return a maturity-aware output type distinguishing coinbase outputs from ordinary confirmed outputs. [4](#0-3)  At minimum, prevent `SignableTransaction::new` from accepting outputs known to be coinbase-immature, and make the API enforce the maturity invariant rather than relying on every caller to implement it. [8](#0-7) 

### Proof of Concept
1. Create a `Scanner` for the wallet's even-y Taproot key using `Scanner::new`. [9](#0-8) 
2. Construct a valid block whose `txdata[0]` coinbase contains an output paying that scanner-registered `script_pubkey`. [10](#0-9) 
3. Call `scanner.scan_block(&block)`; the coinbase output is returned as a `ReceivedOutput` because no coinbase index or maturity check is applied. [4](#0-3) 
4. Pass that `ReceivedOutput` to `SignableTransaction::new`; it is converted into a `TxIn` and its claimed value is committed into the Taproot sighash as though it were spendable. [6](#0-5) [7](#0-6) 
5. The resulting spend is consensus-invalid until the coinbase reaches maturity, while the scanner already reported the output as received. [1](#0-0)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L158-165)
```rust
impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L198-214)
```rust
  /// Scan a transaction.
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

**File:** networks/bitcoin/src/wallet/send.rs (L373-390)
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
```
