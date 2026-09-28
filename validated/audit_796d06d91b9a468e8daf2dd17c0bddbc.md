### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` iterates over every transaction in a block, including `txdata[0]` (the coinbase), and passes each through `scan_transaction`, which matches purely on `script_pubkey` against registered scripts. Any output paying to a watched script — including a coinbase output — is returned as a `ReceivedOutput` indistinguishable from a normal, immediately-spendable output. Coinbase outputs are unspendable for 100 blocks by Bitcoin consensus, so the scanner reports funds as received which are not (yet) spendable, with nothing downstream able to distinguish them.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `scan_transaction` matches `output.script_pubkey` against `self.scripts` and emits a `ReceivedOutput` containing only `offset`, `output`, and `outpoint` — it carries no marker that the parent transaction was a coinbase. `scan_block` then iterates `block.txdata` wholesale, so `txdata[0]` (always the coinbase) is scanned with the same rules as ordinary transactions. A `ReceivedOutput` derived from a coinbase is serialized, stored, and scheduled identically to a mature output; Bitcoin consensus rejects any transaction spending a coinbase before 100 confirmations, so any plan built on this input produces a transaction the network will reject. [1](#0-0) 

The only mitigation is a doc comment on `scan_block` stating a post-processing pass "is needed" if outputs must be immediately spendable; nothing in the type system or the `ReceivedOutput` structure enforces or even exposes the coinbase-ness, and `scan_block` is the natural, advertised entry point for processing a full block (the test at `networks/bitcoin/tests/wallet.rs` calls it directly on a block and asserts `outputs == scan_transaction(block.txdata[0])`, treating a coinbase output as a first-class received output). [2](#0-1) [3](#0-2) 

This is the direct analog of the gvfs bug class: an endpoint (the block scanner) accepts input from an unauthenticated party (any miner) and performs a privileged action (crediting funds to the multisig's spendable set) without verifying the input is authorized for that action (i.e., is actually spendable under consensus rules).

### Impact Explanation
A miner (or anyone who can get a coinbase crafted, e.g., a mining pool participant) can cause the scanner to report a received output that cannot be spent for 100 blocks. If downstream logic selects it as an input — for a payment, refund, or rotation — the resulting signed transaction is consensus-invalid: `bad-txns-premature-spend-of-coinbase`. Consequences range from a batch being built on an unspendable input (funds incorrectly accounted as usable liquidity) to a signed-but-unbroadcastable transaction, and in refund/forwarding flows it can strand accounting. This satisfies the accepted criterion "funds reported received that are not spendable."

### Likelihood Explanation
Reachability is high within the admitted threat model: the attacker only needs to send a Bitcoin transaction (a coinbase in a block they mine). With pooled mining, getting a coinbase output addressed to an arbitrary script is feasible for miners, and the attack requires no control over Serai participants. However, the impact is time-bounded — the output does become spendable after 100 blocks — and honest scanning of later blocks does not worsen the issue. This bounds severity to Medium.

### Recommendation
In `Scanner::scan_block` (`networks/bitcoin/src/wallet/mod.rs`), either skip `block.txdata[0]` entirely or tag the `ReceivedOutput` with a maturity flag and expose it so callers can filter. The cleanest fix matching the existing API is to iterate `block.txdata[1 ..]` (as the doc comment itself suggests) and, if coinbase deposits must be supported, carry an `is_coinbase`/`maturity_height` field on `ReceivedOutput` so downstream planners can enforce the 100-block delay rather than relying on a documentation caveat.

### Proof of Concept
```rust
// networks/bitcoin: regtest scenario (mirrors tests/wallet.rs::send_and_get_output)
let scanner = Scanner::new(key).unwrap();
// Miner mines a block whose coinbase pays directly to the multisig's p2tr script
let block = rpc.get_block(&rpc.get_block_hash(n).await.unwrap()).await.unwrap();
// txdata[0] is the coinbase; scan_block returns its output as a normal ReceivedOutput
let outputs = scanner.scan_block(&block);
assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0));
// outputs[0] is indistinguishable from a spendable output, yet spending it within
// 100 blocks produces a consensus-invalid transaction (bad-txns-premature-spend-of-coinbase)
let tx = SignableTransaction::new(vec![outputs[0].clone()], &payments, None, None, FEE).unwrap();
// sign and broadcast -> rejected by the network
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

**File:** networks/bitcoin/tests/wallet.rs (L63-77)
```rust
  let block = rpc.get_block(&rpc.get_block_hash(block_number).await.unwrap()).await.unwrap();

  let mut outputs = scanner.scan_block(&block);
  assert_eq!(outputs, scanner.scan_transaction(&block.txdata[0]));

  assert_eq!(outputs.len(), 1);
  assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0));
  assert_eq!(outputs[0].value(), block.txdata[0].output[0].value.to_sat());

  assert_eq!(
    ReceivedOutput::read::<&[u8]>(&mut outputs[0].serialize().as_ref()).unwrap(),
    outputs[0]
  );

  outputs.swap_remove(0)
```
