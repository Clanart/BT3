### Title
Scanner reports immature coinbase outputs as spendable `ReceivedOutput`s, enabling false deposit crediting - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes a "false top-up" class: a system credits a deposit that did not actually transfer usable value because the crediting path checks surface evidence (a successful call/event) rather than the spendability of the funds. In Serai's Bitcoin scanner, `Scanner::scan_block` iterates over *all* transactions in a block, including `block.txdata[0]` — the coinbase. A `ReceivedOutput` produced for a coinbase output is labeled "A spendable output" (`networks/bitcoin/src/wallet/mod.rs:88-97`), yet coinbase outputs are unspendable for 100 blocks by consensus. Any consumer that treats scan results as immediately spendable received funds will credit a deposit that cannot actually be spent, and any attempt to build a transaction spending it will produce an invalid transaction.

### Finding Description
`scan_block` (`networks/bitcoin/src/wallet/mod.rs:221-227`) loops over `block.txdata` with no skip of index 0 and no `tx.is_coinbase()` check:

```rust
// networks/bitcoin/src/wallet/mod.rs:221-227
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

`scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:199-214`) matches only on `output.script_pubkey` and pushes a `ReceivedOutput` for any match. The struct itself is documented as "A spendable output" and carries only `offset`, `output`, and `outpoint` — there is no maturity/confirmations metadata, so nothing downstream can distinguish an immature coinbase output from a normal payment.

The docstring on `scan_block` acknowledges the hazard ("a post-processing pass is needed"), but the API still performs no enforcement: the coinbase is folded into the same `Vec<ReceivedOutput>` type documented as spendable. Unlike the LDO case where the failure is a silent `false`, here the failure is a silent "received and spendable" for an output the consensus rules forbid spending.

### Impact Explanation
A coinbase output paying to a registered Serai script (the plain tweaked key or any registered offset) is reported as a received, spendable output while being unspendable for 100 blocks. Consequences:

- Funds reported received that are not spendable: if the deposit path mints against the reported `ReceivedOutput`, the minted claim is temporarily backed by an output the wallet cannot actually move, and any immediate spend attempt fails (Bitcoin rejects immature coinbase spends at mempool/consensus level).
- Accounting distortion: the reported `value()` counts toward spendable balance although it cannot contribute to a transaction's inputs.

This mirrors the fake-deposit class: a value-transfer signal is accepted at face value while the underlying funds are not actually usable as reported.

### Likelihood Explanation
Reachability requires an unprivileged party to get a coinbase output paying to a Serai-scanned script. Coinbase outputs are created by whoever mines a block and can pay arbitrary script_pubkeys. A miner (or a pool that lets miners set coinbase outputs) can include a payment to Serai's scanned key in `txdata[0]` of their own block — no cooperation from Serai nodes or validators is required. This is not free (it requires mining a block), but it is fully permissionless, and the failure also triggers accidentally (e.g., a pool paying a deposit-address script as a payout). Severity is Medium: the credited funds are real BTC but immature, so the practical impact is failed/delayed spends and miscredited "spendable" balances rather than permanent theft.

### Recommendation
In `Scanner::scan_block` (`networks/bitcoin/src/wallet/mod.rs:221`), skip the coinbase transaction: iterate `&block.txdata[1 ..]` (guarding against empty `txdata`), or check `tx.is_coinbase()` and either drop it or mark the resulting `ReceivedOutput` as immature. If coinbase outputs are intentionally supported, carry a maturity flag/block height on `ReceivedOutput` so consumers can enforce the 100-block rule, and have the spend-construction path reject immature outpoints.

### Proof of Concept
1. Construct a `Scanner` via `Scanner::new(key)`; let `s` be the registered `p2tr_script_buf(key)` script.
2. Build a `Block` where `txdata[0]` is a coinbase transaction whose `output[0]` has `script_pubkey == s` and `value == 50_00000000` (or any amount).
3. Call `scanner.scan_block(&block)`.
4. Result: a `ReceivedOutput` is returned with `outpoint = (coinbase_txid, 0)`, indistinguishable from a normal payment output, despite `value()` being unspendable for 100 blocks.
5. Feeding this `ReceivedOutput` into the send path (`networks/bitcoin/src/wallet/send.rs`) produces a transaction spending an immature coinbase, which `bitcoind` rejects with a "bad-txns-premature-spend-of-coinbase"-style error, while the balance/deposit accounting already treated the funds as received and spendable.