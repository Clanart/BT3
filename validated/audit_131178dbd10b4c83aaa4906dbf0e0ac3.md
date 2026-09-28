### Title
Coinbase outputs are reported as received before they are spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` scans every transaction in `block.txdata`, including the coinbase transaction, and returns matching outputs as ordinary `ReceivedOutput`s. The implementation explicitly notes that these outputs remain bound by Bitcoin coinbase maturity, but does not exclude them. [1](#0-0) 

### Finding Description
`scan_transaction` accepts any transaction and creates a `ReceivedOutput` whenever an output’s `script_pubkey` matches a registered Serai script. [2](#0-1)  `scan_block` applies that logic to all transactions without special-casing `block.txdata[0]` or checking whether the transaction is a coinbase. [3](#0-2)  Bitcoin requires coinbase outputs to reach 100 confirmations before spending, while the Bitcoin network integration uses only six confirmations. [4](#0-3)  The processor collects each active key’s outputs and forwards non-dust outputs into the emitted block event without an additional maturity check shown in this path. [5](#0-4) [6](#0-5) 

### Impact Explanation
A miner can direct a coinbase output to Serai’s P2TR address. After Serai’s six-confirmation threshold, the scanner can report the output as received even though consensus rejects any transaction spending it until block depth 100. This can credit or schedule value that cannot yet be spent, causing a premature spend to be rejected and delaying access to the reported funds.

### Likelihood Explanation
Exploitation requires control over a coinbase transaction, so it is limited to miners or mining pools rather than arbitrary transaction senders. No validator compromise, private-key access, or malformed cryptographic input is required; the attacker only needs to mine a valid block paying a tracked Serai script.

### Recommendation
Exclude coinbase transaction outputs in `scan_block`, or annotate `ReceivedOutput` with maturity state and prevent scheduling or signing until the output is at least 100 blocks old. The lowest-level fix is to iterate over `block.txdata[1 ..]`; the more flexible fix is to preserve coinbase outputs for reporting while making spendability explicit.

### Proof of Concept
1. Obtain a tracked Serai P2TR script corresponding to `Scanner::new(key)` or a securely registered offset.
2. Mine a Bitcoin block whose coinbase transaction contains an output with that `script_pubkey` and value at least `Bitcoin::DUST`.
3. After six confirmations, call `Scanner::scan_block` on the block.
4. The scanner returns a `ReceivedOutput` for the coinbase output because it only compares `output.script_pubkey` against registered scripts and records the transaction ID and vout. [7](#0-6) 
5. Construct a spend using that `ReceivedOutput`; the transaction is consensus-invalid until the coinbase reaches 100 confirmations, despite Serai having reported the funds as received.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L198-210)
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
```

**File:** networks/bitcoin/src/wallet/mod.rs (L216-220)
```rust
  /// Scan a block.
  ///
  /// This will also scan the coinbase transaction which is bound by maturity. If received outputs
  /// must be immediately spendable, a post-processing pass is needed to remove those outputs.
  /// Alternatively, scan_transaction can be called on `block.txdata[1 ..]`.
```

**File:** networks/bitcoin/src/wallet/mod.rs (L221-226)
```rust
  pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {
      res.extend(self.scan_transaction(tx));
    }
    res
```

**File:** processor/src/networks/bitcoin.rs (L601-605)
```rust
  const NETWORK: ExternalNetworkId = ExternalNetworkId::Bitcoin;
  const ID: &'static str = "Bitcoin";
  const ESTIMATED_BLOCK_TIME_IN_SECONDS: usize = 600;
  const CONFIRMATIONS: usize = 6;

```

**File:** processor/src/multisigs/scanner.rs (L559-566)
```rust
          let key_vec = key.to_bytes().as_ref().to_vec();

          // TODO: These lines are the ones which will cause a really long-lived lock acquisition
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
```

**File:** processor/src/multisigs/scanner.rs (L697-705)
```rust
        let sent_block = if has_activation || is_retirement_block || (!outputs.is_empty()) {
          // Save the outputs to disk
          let mut txn = db.txn();
          ScannerDb::<N, D>::save_outputs(&mut txn, &block_id, &outputs);
          txn.commit();

          // Send all outputs
          if !scanner.emit(ScannerEvent::Block { is_retirement_block, block: block_id, outputs }) {
            return;
```
