### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s - ([File: networks/bitcoin/src/wallet/mod.rs])

### Summary
`Scanner::scan_block` iterates over the entire `block.txdata`, including `txdata[0]` — the coinbase transaction. A miner can pay the multisig's P2TR script in a coinbase output, and the scanner will emit it as a normal `ReceivedOutput`. Coinbase outputs are consensus-immature for 100 blocks, so any attempt to spend them via `SignableTransaction` produces a transaction the network rejects — the exact analog of funds accepted by the payable entry point but never becoming usable.

### Finding Description
The external report describes `Allo.registerRecipient` accepting `msg.value` without forwarding it, leaving funds stranded. The analogous shape in Serai is the Bitcoin scanner accepting output value into the spendable set without checking spendability:

- `Scanner::scan_block` at `networks/bitcoin/src/wallet/mod.rs:221-227` loops `for tx in &block.txdata` with no exclusion of `txdata[0]`, delegating to `scan_transaction`.
- `scan_transaction` (`mod.rs:199-214`) matches purely on `output.script_pubkey` (`mod.rs:205`), so a coinbase output paying a registered script produces an ordinary `ReceivedOutput` carrying the correct `offset`/`outpoint`.
- The only guard is a doc comment (`mod.rs:218-220`) stating coinbase outputs "are bound by maturity" and callers "need" a post-processing pass — no code enforces this, and the processor-facing path scans whole blocks.
- Downstream, `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) treats every `ReceivedOutput` as a valid input, and `multisig` (`send.rs:273-286)`/`taproot_key_spend_signature_hash` (`send.rs:386`) sign spends for it unconditionally. A spend including an immature coinbase input fails Bitcoin's `COINBASE_MATURITY` consensus check at relay/inclusion time.

No equivalent parity/validity gap was found in `multisig` itself (the `p2tr_script_buf(offset.group_key())` check at `send.rs:277` correctly rejects mismatched offsets), and the fee/change math correctly routes sub-dust leftovers to fees by design.

### Impact Explanation
Any miner (an unprivileged party controlling coinbase outputs) can cause the multisig wallet to report received funds that are not spendable for 100 blocks. Concretely: the output enters `scanner.ram_outputs` and the scheduler's input set; when `SignableTransaction`/`TransactionMachine` include it, the fully-signed transaction is rejected by the network, stalling the signing round or burning the fee-estimation attempt, and the balance is misrepresented until maturity. This matches the accepted-impact category "funds reported received that are not spendable."

### Likelihood Explanation
Low-to-moderate. It requires a miner to deliberately (or incidentally, via pool payout behavior) pay the Serai P2TR script in a coinbase. Miners are arbitrary unprivileged actors, and paying a random address costs them nothing extra. The effect is a temporary liveness/accounting fault rather than theft, consistent with Medium severity.

### Recommendation
In `Scanner::scan_block`, skip `block.txdata[0]` (or return `scan_transaction` results only for `block.txdata[1 ..]`), matching the documented intent. Alternatively, track maturity: mark `ReceivedOutput`s originating from coinbase transactions and have the scheduler exclude them until `current_height - coinbase_height >= 100`.

### Proof of Concept
```rust
// In networks/bitcoin, with a registered Scanner for `key`:
let coinbase_tx = Transaction {
  // ... a valid coinbase with an output paying p2tr_script_buf(key)
};
let block = Block { /* header */, txdata: vec![coinbase_tx, /* ... */] };

let outputs = scanner.scan_block(&block);
// outputs[0] is a ReceivedOutput for the coinbase outpoint — reported received
// but unspendable for 100 confirmations.

let signable = SignableTransaction::new(
  outputs, &payments, Some(change), None, fee_per_vbyte,
).unwrap();
// Signing succeeds; broadcasting fails: bad-txns-premature-spend-of-coinbase.
```
The comment at `networks/bitcoin/src/wallet/mod.rs:216-220` confirms coinbase transactions are scanned; `scan_transaction` at `mod.rs:205` matches script_pubkey only, so the coinbase output is indistinguishable from a mature deposit.