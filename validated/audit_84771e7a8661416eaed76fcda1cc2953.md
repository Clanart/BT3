### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The analogous bug class is a capability asymmetry: a component that can move/report value in one direction without the complementary path being valid. In `bitcoin-serai`, `Scanner::scan_block` iterates over **all** transactions in a block, including the coinbase transaction (`block.txdata[0]`), and returns matching outputs as fully-formed `ReceivedOutput`s. Coinbase outputs are encumbered by Bitcoin's 100-block maturity rule (BIP-30/consensus `COINBASE_MATURITY`): they appear in `tx.output`, match the scanner's `script_pubkey` set, and are indistinguishable from spendable outputs in the returned `Vec<ReceivedOutput>` — but any transaction spending one is consensus-invalid until maturity.

### Finding Description
`scan_block` at `networks/bitcoin/src/wallet/mod.rs:221-227` calls `scan_transaction` for every transaction, including index 0:

```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {           // includes txdata[0], the coinbase
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

`scan_transaction` (`mod.rs:199-214`) matches purely on `output.script_pubkey` against registered scripts, with no check that the containing transaction is a coinbase (`tx.is_coinbase()`) and no maturity tracking. The resulting `ReceivedOutput` carries a valid `offset`, `TxOut`, and `OutPoint`, so it flows directly into `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150`) which performs no coinbase/maturity validation either — it happily builds inputs, computes `Prevouts::All` sighashes, and the FROST machines produce a fully-signed transaction that the network will reject.

An unprivileged miner (or any party that can cause a coinbase to pay to a watched script — e.g., a mining pool paying out to Serai's deposit/branch address, a documented and realistic occurrence) causes the scanner to report funds as received that are not yet spendable. The only mitigation is a doc comment stating "a post-processing pass is needed" — the API itself returns unspendable outputs in the same type as spendable ones, and nothing downstream enforces the distinction.

### Impact Explanation
Any consumer that takes `scan_block` results and feeds them to `SignableTransaction::new` (the intended pipeline, exercised exactly this way in `networks/bitcoin/tests/wallet.rs`) will construct and threshold-sign a consensus-invalid transaction. This yields: funds incorrectly reported as received/spendable (balance inflation in the accounting layer), wasted signing rounds producing a transaction that cannot be broadcast, and a liveness failure — if the immature output is batched with valid inputs, the entire signed transaction is invalid and must be reconstructed. Downstream consumers (e.g., `processor`) classify outputs by scanning; a `ReceivedOutput` that cannot be spent corrupts the spendable-UTXO set until manually filtered.

### Likelihood Explanation
Medium. Coinbase outputs paying to arbitrary third-party scripts are common (mining pool payouts to the pool's address). Any time a watched script coincides with a miner's payout address — or an attacker deliberately mines/donates a coinbase output to a Serai address — `scan_block` emits an unspendable `ReceivedOutput`. Whether it becomes a signed invalid transaction depends on the integrator implementing the documented-but-unenforced post-processing pass; the library provides no guard.

### Recommendation
Skip the coinbase transaction in `scan_block` when maturity is required, or make maturity explicit in the API:

```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata[1 ..] {   // exclude the immature coinbase
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

Alternatively, add a `scan_block_including_coinbase` variant returning outputs tagged with their maturity height, or store a `coinbase: bool` flag on `ReceivedOutput` so `SignableTransaction::new` can reject immature inputs.

### Proof of Concept
```rust
// Regtest: mine a block paying the coinbase reward to the scanner's P2TR script
let block_hash = rpc
  .rpc_call::<Vec<String>>(
    "generatetoaddress",
    serde_json::json!([1, Address::from_script(&p2tr_script_buf(key).unwrap(), Network::Regtest).unwrap()]),
  )
  .await.unwrap();
let block = rpc.get_block(&block_hash[0]).await.unwrap();

let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1);                       // reported as received
assert_eq!(outputs[0].offset(), Scalar::ZERO);      // looks fully spendable

// Feed it into the spend pipeline — no maturity check exists
let tx = SignableTransaction::new(
  outputs,
  &[(p2tr_script_buf(key).unwrap(), 1000)],
  Some(change_addr), None, FEE,
).unwrap();
let signed = sign(&keys, &tx);  // produces a fully-signed tx...

// ...that the network rejects: "bad-txns-premature-spend-of-coinbase"
assert!(rpc.send_raw_transaction(&signed).await.is_err());
```

The existing test suite already demonstrates the first half: `send_and_get_output` (`networks/bitcoin/tests/wallet.rs:40-78`) scans a coinbase via `scan_block`, asserts `outputs.len() == 1`, and only succeeds because it then mines 100 additional blocks before spending — implicitly confirming the output was immature at scan time.