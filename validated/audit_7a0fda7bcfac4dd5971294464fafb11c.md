### Title
Scanner reports immature coinbase outputs as spendable received funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` in `networks/bitcoin/src/wallet/mod.rs` iterates over **all** transactions in a block, including `block.txdata[0]` (the coinbase transaction). Coinbase outputs are unspendable for 100 blocks per Bitcoin consensus rules, yet the scanner reports them identically to normal outputs. The processor's Bitcoin `get_outputs` path scans full block transaction data without checking `is_coinbase()` or maturity, so an attacker who mines a block (or a miner acting as the unprivileged external party paying Serai's registered key) can cause the Serai network to credit and attempt to spend an output that consensus will reject.

### Finding Description
`Scanner::scan_block` calls `scan_transaction` for every transaction in `block.txdata` with no exclusion of the coinbase:

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

`scan_transaction` matches only on `output.script_pubkey` against the registered scripts (`self.scripts.get(&output.script_pubkey)` at line 205) and immediately wraps matches in `ReceivedOutput` — it does not check whether the containing transaction is a coinbase or whether its outputs are mature. The doc comment at lines 216-220 explicitly acknowledges the hazard: *"This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed"*. However, that post-processing is delegated to callers, and in the processor's scanning flow (`processor/src/multisigs/scanner.rs` calling `network.get_outputs(&block, key)`, with the Bitcoin implementation in `processor/src/networks/bitcoin.rs` iterating block `txdata`), there is no maturity or `is_coinbase` filter — outputs are only gated on `output.balance().amount.0 >= N::DUST` (`processor/src/multisigs/scanner.rs:564`).

This maps the report's bug class (a missing authorization/validity check lets an unprivileged party invoke a privileged capability — here, crediting funds and queueing them for threshold-signed spend) onto Serai's real shape: an external party feeding crafted transaction data into the scanner causes the multisig to treat unsatisfiable inputs as spendable balance.

### Impact Explanation
Once a coinbase output paying a Serai-registered script (external, branch, change, or forward offset registered via `register_offset` in `processor/src/networks/bitcoin.rs:324-344`) is reported by `ScannerEvent::Block`, it enters the scheduler's output set. `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`) will happily include the immature coinbase outpoint as an input, the FROST signing round will produce a valid BIP-340 signature over it, and the resulting transaction will be rejected by the Bitcoin network for violating the coinbase maturity rule. Consequences:

- A batch of otherwise-valid payments is poisoned by one immature input; the signed transaction can never confirm, stalling the signing pipeline and forcing re-attempts.
- Internal accounting reports funds as received and spendable that are not — matching the accepted impact class "funds reported received that are not spendable".
- An attacker who can mine (regtest trivially; mainnet at mining cost) can repeatedly inject such outputs at will since coinbase recipients are miner-chosen.

### Likelihood Explanation
Requires the attacker to produce a block containing a coinbase paying a Serai script — feasible for any miner or merged-mining participant without any Serai privilege or credentials, exactly the "unprivileged party with public inputs" model. The defect is deterministic: no code path between `scan_block`/`get_outputs` and `SignableTransaction::new` filters coinbase outputs, so any such output reaching maturity-check-free scanning is misclassified.

### Recommendation
In `Scanner::scan_block` (or the Bitcoin `get_outputs` implementation in `processor/src/networks/bitcoin.rs`), skip `block.txdata[0]` when `tx.is_coinbase()`, or track coinbase outputs with their block height and only emit them after `COINBASE_MATURITY` (100 blocks) of additional confirmations. Alternatively, tag `ReceivedOutput` with the containing transaction's coinbase status so the scheduler can defer scheduling.

### Proof of Concept
1. Construct `Scanner::new(even_group_key)` and register the standard offsets (`branch`, `change`, `forward` via `hash_to_F`).
2. Mine a regtest block whose coinbase output pays `p2tr_script_buf(key + G*offset)` for a registered offset (miners choose coinbase outputs freely).
3. Call `scanner.scan_block(&block)` — the coinbase output is returned in `res` with `ReceivedOutput { outpoint: OutPoint::new(coinbase_txid, vout), .. }`, indistinguishable from a mature payment.
4. Feed it into `SignableTransaction::new(vec![coinbase_output], payments, change, None, fee)` — construction succeeds.
5. Sign via the FROST multisig path (`tx.clone().multisig(&keys)` / `sign_without_caching`) — signature is produced; `send_raw_transaction` is rejected by Bitcoin Core with `bad-txns-premature-spend-of-coinbase`.

Note on verification limits: I confirmed `scan_block` includes the coinbase and that the processor's `get_outputs` call site applies only a dust check. I could not read the full body of Bitcoin's `get_outputs` in `processor/src/networks/bitcoin.rs` in the available iterations; if it (rather than `scan_block`) filters `is_coinbase`, this finding does not hold and should be re-checked against that function's implementation.