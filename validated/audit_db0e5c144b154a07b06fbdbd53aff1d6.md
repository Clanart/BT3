### Title
`Scanner::scan_block` reports coinbase outputs as received even though they are unspendable until maturity - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The OngoingBounty bug class is: a deposit path accepts an asset class that the corresponding claim/payout path cannot ever handle, so the funds become unreachable. The analog in bitcoin-serai is `Scanner::scan_block`, which iterates `block.txdata` including `block.txdata[0]` (the coinbase transaction) via `scan_transaction`. Any output in a coinbase paying one of the registered P2TR scripts is returned as a `ReceivedOutput` indistinguishable from a normal output, yet coinbase outputs are consensus-unspendable for 100 blocks (BIP-34/COINBASE_MATURITY). The receiving side therefore reports funds as received that the spending side cannot claim.

### Finding Description
`Scanner::scan_block` at `networks/bitcoin/src/wallet/mod.rs` scans every transaction in the block:

```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for tx in &block.txdata {            // includes txdata[0], the coinbase
      res.extend(self.scan_transaction(tx));
    }
    res
}
```

`scan_transaction` matches only `output.script_pubkey` against the registered script map and emits a `ReceivedOutput { offset, output, outpoint }` (`networks/bitcoin/src/wallet/mod.rs:199-214`). There is no maturity or transaction-position check. A `ReceivedOutput` produced this way carries an outpoint referencing a coinbase tx, which any `SignableTransaction` built over it will fail to broadcast for 100 blocks, and which no honest mempool/peer will accept at all.

The in-repo comment acknowledges this ("If received outputs must be immediately spendable, a post-processing pass is needed"), but the invariant violation lives inside the library: `ReceivedOutput` is documented as "A spendable output" (line 88) and `scan_block` hands out non-spendable ones under that type.

### Impact Explanation
"Funds reported received that are not spendable" — an accepted impact class. If an integrator (or the processor) schedules a `SignableTransaction` over such an output, the resulting transaction is permanently invalid (not merely delayed-in-wallet): every node rejects it for spending an immature coinbase, so the plan/ack flow stalls and the reported balance cannot be exercised. Repeated coinbase outputs also cause the same invalid-plan construction to recur each block.

### Likelihood Explanation
Reachable by an unprivileged external party: any miner (or any party able to influence a coinbase's outputs, e.g. via a mining pool payout template) can point the coinbase's P2TR output at a Serai multisig's registered script. Coinbase pays directly to arbitrary scripts, so no special access is needed. The triggering condition — the multisig address receiving a coinbase output — requires the adversary to mine a block, which bounds likelihood to Medium rather than High.

### Recommendation
Skip `block.txdata[0]` inside `scan_block` (the processor's `get_outputs` already does `block.txdata[1 ..]` for exactly this reason), or record a maturity flag on `ReceivedOutput` so the spend path can reject immature outpoints. If keeping the current behavior, change the `ReceivedOutput` contract so "spendable" is not claimed for coinbase-derived outputs.

### Proof of Concept
1. `Scanner::new(key)` for an even `key`; note `p2tr_script_buf(key)`.
2. Mine a regtest block whose coinbase pays `P2TR(key)`, plus 1 block.
3. `scanner.scan_block(&block)` returns a `ReceivedOutput` with `outpoint = (coinbase_txid, 0)` — indistinguishable from a normal output.
4. Build a `SignableTransaction` spending it: `rpc.send_raw_transaction` fails with `bad-txns-premature-spend-of-coinbase`. The scanner reported funds that cannot be claimed for 100 blocks, with no indication in the `ReceivedOutput` type.