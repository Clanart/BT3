### Title
Scanner reports immature coinbase outputs as spendable received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over **every** transaction in a block — including `block.txdata[0]`, the coinbase — and returns any output whose `script_pubkey` matches a registered offset as a `ReceivedOutput` that is immediately indistinguishable from a normal spendable output. A miner (an unprivileged external party, analogous to the "Overall/Read" user in the Jenkins advisory who can trigger actions they are not entitled to) can pay Serai's tweaked key inside a coinbase transaction and cause the scanner to report funds as received that are consensus-unspendable for 100 blocks.

### Finding Description
`scan_transaction` matches only on `output.script_pubkey` against the `scripts` map and builds a `ReceivedOutput` with no contextual information about the transaction's position or type (`networks/bitcoin/src/wallet/mod.rs:199-214`). `scan_block` then calls it on `block.txdata[0]` as well as all later transactions (`mod.rs:221-227`):

```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {          // includes txdata[0], the coinbase
      res.extend(self.scan_transaction(tx));
    }
    res
}
```

A coinbase output is unspendable until the block is 100 deep (Bitcoin consensus COINBASE_MATURITY rule). Nothing in `ReceivedOutput` records that the outpoint came from a coinbase — it carries only `offset`, `output`, `outpoint` (`mod.rs:90-97`), and downstream `SignableTransaction::new`/`multisig` in `send.rs` will happily build and sign a transaction spending it (`send.rs:150-256`, `273-285`). The doc comment at `mod.rs:216-220` notes the caveat ("a post-processing pass is needed"), but the API itself performs no check, and nothing enforces that the post-processing actually happens — the "authorization" of the action (treating the output as spendable) is implicit and unchecked.

### Impact Explanation
- The wallet/registry layer records a deposit that cannot actually be spent. Depending on the consumer, this can cause credited-but-unspendable funds, and any attempt to spend it produces a transaction rejected by the network until maturity.
- With `Prevouts::All` in `TransactionSignMachine::sign` (`send.rs:375`), an invalid prevout in the input set invalidates the **whole** transaction, so one immature coinbase input poisons a batch of otherwise valid spends (DoS on withdrawals) until it matures or is filtered.

### Likelihood Explanation
Any Bitcoin miner — or anyone able to influence a coinbase's outputs (e.g., a mining pool's payout policy) — can direct a coinbase output to Serai's P2TR script at negligible cost. This requires no Serai privileges, only the ability to submit a Bitcoin transaction, which matches the report's threat model of an unprivileged party initiating a privileged-side effect.

### Recommendation
Track maturity in the scan results: have `scan_block` (or a `scan_block_with_height` variant) either skip `block.txdata[0]`, or flag `ReceivedOutput`s originating from the coinbase so callers must wait 100 confirmations before passing them to `SignableTransaction::new`. Do not rely on an undocumented-at-the-call-site post-processing pass.

### Proof of Concept
```rust
// Conceptual: a block whose coinbase pays Serai's registered tweaked key
let mut scanner = Scanner::new(tweaked_group_key).unwrap();
let offset = scanner.register_offset(offset_scalar).unwrap();

// Miner crafts coinbase with vout paying p2tr_script_buf(key + offset*G)
let block: Block = miner_block_with_coinbase_paying(serai_script);

let received = scanner.scan_block(&block);
// `received` contains a ReceivedOutput for the coinbase outpoint.
// SignableTransaction::new(vec![received[0]], payments, ...) succeeds and
// produces a signed transaction that every Bitcoin node rejects:
// "bad-txns-premature-spend-of-coinbase".
```

Key code locations: `scan_block` (`networks/bitcoin/src/wallet/mod.rs:221-227`), `scan_transaction` (`mod.rs:199-214`), `ReceivedOutput` (`mod.rs:90-97`), spending path (`networks/bitcoin/src/wallet/send.rs:150-285`).