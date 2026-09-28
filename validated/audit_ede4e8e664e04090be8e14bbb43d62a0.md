### Title
Funds sent to Serai's Bitcoin address inside a coinbase transaction are permanently unspendable - (File: processor/src/networks/bitcoin.rs)

### Summary
Analogous to BathBuddy accepting ETH it can never release, Serai's Bitcoin output pipeline can receive funds it can never spend. `Bitcoin::get_outputs` unconditionally skips `block.txdata[0]` (the coinbase) when scanning a block, so any output paying to a Serai multisig script that lands inside a coinbase transaction is never emitted as an `Output`, never enters the scheduler's input set, and is never re-scanned — permanently locking those funds.

### Finding Description
`Scanner::scan_block` in `bitcoin_serai` scans every transaction in a block, and its own docs acknowledge that coinbase outputs are "bound by maturity" — i.e., delayed, not unspendable. [1](#0-0) 

However, the processor's `get_outputs` does not defer maturity handling; it simply drops the entire coinbase transaction:

```rust
// processor/src/networks/bitcoin.rs
// Skip the coinbase transaction which is burdened by maturity
for tx in &block.txdata[1 ..] {
  for output in scanner.scan_transaction(tx) { ... }
}
``` [2](#0-1) 

After 100 blocks the coinbase output is fully mature and spendable by the threshold key (`ReceivedOutput` carries the `offset` needed to derive the spending key via `keys.offset(offset)`), [3](#0-2) [4](#0-3)  yet the scanner walks blocks strictly forward and will never revisit `txdata[0]` of a past block. [5](#0-4)  The output is therefore received on-chain but permanently excluded from Serai's spendable set.

A secondary instance of the same class: outputs below `N::DUST` are silently dropped (`if output.balance().amount.0 >= N::DUST`), even though a sub-dust *input* is consensus-valid to spend — dust rules constrain outputs, not inputs — so e.g. a 400-sat P2TR payment (above Bitcoin's 330-sat P2TR dust relay limit) is received but never spendable. [6](#0-5) 

### Impact Explanation
Any Bitcoin payer — most realistically a mining pool that pays out directly in a coinbase transaction (a common practice for pools paying miners, which could include a pool paying a Serai-based deposit/withdrawal address) — causes funds to be credited on-chain to a Serai multisig script with no path for the threshold network to ever observe or spend them. The funds are locked forever, exactly mirroring the BathBuddy "receives but cannot release" class. Coinbase outputs can be arbitrarily large (block subsidy + fees), so the locked amount is unbounded.

### Likelihood Explanation
Requires the paying party to place the Serai output in a coinbase transaction rather than a regular transaction. Ordinary wallets cannot do this, but mining pools routinely pay addresses directly from coinbase outputs, so a user supplying a Serai deposit address to a pool is a realistic trigger. No attacker sophistication is needed; the payer may even be doing it innocently. Accordingly: Medium.

### Recommendation
Scan the coinbase transaction but gate its outputs on maturity: either (a) emit coinbase-matched outputs only once the block is at least 100 confirmations deep (re-check `txdata[0]` when processing later blocks, or queue them for delayed emission), or (b) track scanned coinbase outputs and emit them in `get_outputs` once matured. At minimum, if coinbase outputs are deliberately unsupported, `external_address`/deposit flows should document that paying via coinbase results in permanent loss, and ideally such outputs should be detected and logged/alerted rather than silently skipped. For the dust filter, consider emitting sub-dust outputs so they can at least be consolidated as inputs later, or explicitly documenting the loss.

### Proof of Concept
1. Serai registers key `K` with the processor scanner; the external deposit script is `p2tr_script_buf(K)`.
2. A mining pool (or any miner) mines a block whose coinbase (`block.txdata[0]`) contains a `TxOut` paying `p2tr_script_buf(K)` for amount `V`.
3. `Bitcoin::get_outputs` iterates `&block.txdata[1 ..]` only, so `scanner.scan_transaction` is never invoked on the coinbase; no `Output` is produced. [7](#0-6) 
4. The scanner advances `ram_scanned`/saved block height forward; no code path ever re-scans `txdata[0]` of a historical block. [8](#0-7) 
5. After 100 blocks the output is consensus-spendable by `K`'s threshold key (offset `Scalar::ZERO`), but it was never reported, so `V` is permanently locked — received yet unspendable.

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

**File:** processor/src/networks/bitcoin.rs (L686-700)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
        assert_eq!(output.tx_id(), tx.id());
        outputs.push(output);
      }
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

**File:** processor/src/multisigs/scanner.rs (L550-567)
```rust
        for (activation_number, key) in scanner.keys.clone() {
          if activation_number > block_being_scanned {
            continue;
          }

          if activation_number == block_being_scanned {
            has_activation = true;
          }

          let key_vec = key.to_bytes().as_ref().to_vec();

          // TODO: These lines are the ones which will cause a really long-lived lock acquisition
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
          }
```

**File:** processor/src/multisigs/scanner.rs (L721-723)
```rust
        // Update ram_scanned
        scanner.ram_scanned = Some(block_being_scanned);

```
