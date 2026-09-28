### Title
Scanner reports coinbase (immature) outputs as received spendable funds — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
Analogous to `borgCore.checkTransaction()` failing to restrict/validate the transferred value in one mode, `Scanner::scan_block` / `Scanner::scan_transaction` report every output matching a registered `script_pubkey` with no spendability or value check — including the coinbase transaction, whose outputs are consensus-locked for 100 blocks. A `ReceivedOutput` produced this way flows directly into `SignableTransaction::new`/`multisig`, which will happily build and sign a transaction spending an immature coinbase output, producing a transaction the network will reject.

### Finding Description
`Scanner::scan_transaction` iterates `tx.output` and returns a `ReceivedOutput` whenever `output.script_pubkey` is in `self.scripts` (mod.rs:199-214). It performs no checks on `output.value` (dust/zero-value outputs are accepted) and no check on the transaction type.

`Scanner::scan_block` feeds **all** transactions, including `block.txdata[0]` (the coinbase), into `scan_transaction` (mod.rs:221-227). Coinbase outputs cannot be spent until 100 confirmations (BIP-30/consensus maturity rule). A miner can send the coinbase reward to a scanned P2TR script — which is exactly what happens in normal mining pool payouts — and the scanner returns it as a `ReceivedOutput` indistinguishable from a spendable one.

`SignableTransaction::new` (send.rs:150-256) and `multisig` (send.rs:273-285) validate only that the prevout's `script_pubkey` matches the offset group key. Nothing verifies the prevout is mature or even exists as claimed — `Prevouts::All` binds the claimed values into the sighash, so a fabricated/mismatched `ReceivedOutput` yields a signature over a transaction consensus-invalid or unbroadcastable.

### Impact Explanation
Funds are reported received that are not spendable. Any downstream consumer that treats `scan_block` results as spendable inputs will construct and threshold-sign a transaction spending an immature coinbase output; the signed transaction is rejected by the network, effectively freezing the intended payment flow until the output matures. Additionally, `ReceivedOutput::read` accepts arbitrary `TxOut` values and outpoints with no on-chain validation, and `multisig` only cross-checks `script_pubkey`, so untrusted serialized outputs feed directly into the sighash-committed prevout set.

The doc comment ("a post-processing pass is needed to remove those outputs") acknowledges the hazard but the API still emits the unspendable output, placing an undocumented-in-code invariant on every caller.

### Likelihood Explanation
Mining pools and anyone paying a Serai deposit address directly from a coinbase produce exactly this shape. It requires only a public Bitcoin transaction to a scanned address — fully reachable by an unprivileged party.

### Recommendation
In `Scanner::scan_block`, skip `block.txdata[0]` (or track maturity and withhold coinbase outputs), and in `scan_transaction`/`SignableTransaction::new`, reject outputs below the spendable dust threshold so that reported `ReceivedOutput`s are actually spendable inputs.

### Proof of Concept
```rust
// Mine a block paying the scanned P2TR key directly in the coinbase
// (as done in networks/bitcoin/tests/wallet.rs send_and_get_output).
let block = rpc.get_block(&rpc.get_block_hash(height).await.unwrap()).unwrap();

// scan_block includes txdata[0]; the coinbase output is returned
let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1); // reported as received

// Constructing + signing a spend of this output succeeds locally...
let tx = SignableTransaction::new(
  outputs.clone(), &payments, None, None, FEE
).unwrap();
let signed = sign(&keys, &tx); // FROST signs it fine

// ...but the network rejects it: coinbase output is immature
// (bad-txns-premature-spend-of-coinbase)
assert!(rpc.send_raw_transaction(&signed).await.is_err());
```

The test helper `send_and_get_output` in `networks/bitcoin/tests/wallet.rs:40-78` demonstrates the receipt path — it scans `block.txdata[0]`'s coinbase output — and only the test's manual "mine until maturity" loop masks the fact that the returned `ReceivedOutput` was unspendable at scan time.