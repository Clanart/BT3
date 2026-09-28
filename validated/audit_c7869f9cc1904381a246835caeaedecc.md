### Title
Scanner reports immature coinbase outputs as spendable `ReceivedOutput`s, making any transaction spending them consensus-invalid — ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`Scanner::scan_block` in `networks/bitcoin/src/wallet/mod.rs` iterates over **all** transactions in `block.txdata`, including `txdata[0]` — the coinbase transaction. Outputs of a coinbase transaction are bound by the 100-block maturity rule and cannot be spent until 100 confirmations. The scanner reports them as ordinary `ReceivedOutput`s, so the Serai processor will treat immature, unspendable funds as received/spendable. The code itself acknowledges this ("This will also scan the coinbase transaction which is bound by maturity... a post-processing pass is needed"), but `scan_block` performs no such filtering itself. This mirrors the reported bug class — a required action (spending the scanned output) that cannot actually be executed — mapped onto the Bitcoin scanner, where an unprivileged miner can deliver unreachable funds to the protocol.

### Finding Description
`scan_transaction` (`networks/bitcoin/src/wallet/mod.rs`) matches any output whose `script_pubkey` is a registered P2TR script and emits a `ReceivedOutput { offset, output, outpoint }` with no maturity or transaction-type check:

```rust
pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
  ...
  if let Some(offset) = self.scripts.get(&output.script_pubkey) {
    res.push(ReceivedOutput { ... });
  }
}
```

`scan_block` then folds every transaction in the block, including the coinbase:

```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

The doc comment explicitly notes the flaw: *"This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed to remove those outputs. Alternatively, scan_transaction can be called on `block.txdata[1 ..]`."* Nothing in this function enforces either remedy — the exclusion is delegated to callers, and the function exposes no way to distinguish a coinbase-derived `ReceivedOutput` from a normal one (no `is_coinbase` flag is recorded). Any downstream consumer that builds a `SignableTransaction` spending these outpoints produces a transaction rejected by Bitcoin consensus (`bad-txns-premature-spend-of-coinbase`) for up to 100 blocks.

### Impact Explanation
A `ReceivedOutput` is the scanner's claim that funds exist and are spendable. For a coinbase output, that claim is false for ~100 blocks: the swap-to-spend path — assembling inputs into `SignableTransaction::new`, signing the BIP-341 sighashes, and broadcasting — can never complete successfully during the maturity window, exactly the "no function exists to execute the required action" class from the reference report. Downstream accounting will book the deposit; user funds are credited against an output that cannot be moved, and any batch TX incorporating it is invalidated in full (a single immature input poisons the entire transaction, stalling all co-spent outputs).

### Likelihood Explanation
Reachable by any miner: mining a block whose coinbase pays Serai's external P2TR address (or any registered branch/change/forward offset script) is sufficient — no protocol privilege is required. It requires the miner to forgo directing their own subsidy elsewhere, so it carries an opportunity cost, but merged-mining or a miner already earning the subsidy makes this cheap. Probability is moderate-low per block, but the impact when triggered (unspendable inputs poisoning spend transactions) qualifies the finding.

### Recommendation
Do not emit `ReceivedOutput`s for coinbase transactions inside `scan_block` — skip `txdata[0]` internally, or tag `ReceivedOutput` with maturity metadata and have consumers enforce `confirmations >= 100` (the `N::CONFIRMATIONS`-based pipeline in `processor/src/networks/bitcoin.rs` should be verified to reject coinbase-sourced eventualities before they reach `SignableTransaction::new`; I could not fully confirm whether such a filter exists downstream — grep results were truncated, so absence of a downstream guard is not proven). Alternatively, check `tx.input[0].previous_output.is_null()` (coinbase marker) in `scan_block` and drop those results.

### Proof of Concept
1. Miner M mines block B whose coinbase transaction contains a `TxOut` paying `p2tr_script_buf(key)` (Serai's external address, offset `Scalar::ZERO`), which is registered in `Scanner::new`.
2. `Scanner::scan_block(B)` returns `[ReceivedOutput { offset: 0, output: <coinbase TxOut>, outpoint: (<coinbase txid>, vout) }]` — indistinguishable from a spendable deposit.
3. Processor constructs `SignableTransaction::new(vec![that_output], payments, change, None, fee)` and the threshold set produces valid Schnorr signatures over the BIP-341 sighash.
4. Broadcast fails with `bad-txns-premature-spend-of-coinbase`; the signed spend is unexecutable until block B+100 — the spend path "cannot be invoked," matching the reference bug class, and if batched with legitimate UTXOs it stalls them as well.