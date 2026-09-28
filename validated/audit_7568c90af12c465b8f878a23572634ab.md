### Title
Scanner reports immature coinbase outputs as spendable, producing transactions that lock/reject funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` in `networks/bitcoin/src/wallet/mod.rs` scans every transaction in a block, including the coinbase transaction, and returns `ReceivedOutput`s for it without any maturity check. These outputs are consensus-unspendable for 100 blocks, yet they are indistinguishable from normal outputs and are accepted verbatim by `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`, which builds and threshold-signs a transaction spending them.

### Finding Description
The external report describes deposits locked until an epoch-resolution event occurs, with no early exit. The Serai analog is funds that the wallet layer reports as received and spendable but which Bitcoin consensus forbids spending until a later event (coinbase maturity, COINBASE_MATURITY = 100 blocks).

`scan_block` iterates `block.txdata` from index 0, so `txdata[0]` (the coinbase) is scanned identically to regular transactions (`networks/bitcoin/src/wallet/mod.rs:221-227`). A miner — an unprivileged party — can place an output paying to the vault's `p2tr_script_buf(key)` (or a registered offset script) in their coinbase. `scan_transaction` matches on `script_pubkey` only (`mod.rs:205`), so the output is returned as a `ReceivedOutput` tagged with its offset.

Downstream, `SignableTransaction::new` (`send.rs:150-256`) performs no maturity or input-eligibility check: it only validates amounts, dust, fees, and weight. `multisig` (`send.rs:273-285`) then produces a `TransactionMachine` and the FROST signing round signs `taproot_key_spend_signature_hash` over `Prevouts::All`, producing a fully-signed transaction that every node will reject as a consensus violation (bad-txns-premature-spend-of-coinbase). The doc comment on `scan_block` acknowledges outputs are "bound by maturity" but provides no enforcement — the scanner output type carries no maturity flag, and the spend path does not differentiate.

### Impact Explanation
A miner can pad the vault's reported balance with immature outputs. If the scheduler/wallet selects such an output as an input, the resulting threshold-signed transaction is consensus-invalid and unbroadcastable until the coinbase matures. Funds are reported as received/spendable when they are not — the exact "funds locked until a resolution event" shape of the report. At minimum this wastes a full FROST signing round and can stall the output-selection pipeline each time the immature output is picked, repeating until maturity (100 blocks).

### Likelihood Explanation
Reachable by any miner at the cost of forgoing part of a coinbase payout to Serai's script — an unprivileged party controlling only transaction data they put on-chain, matching the reachability rules. It requires a miner to deliberately target the vault's script_pubkey, but no collusion, leaked keys, or malicious validator is needed. Impact is bounded (delayed availability rather than permanent loss), fitting Medium.

### Recommendation
Have `scan_block` skip `block.txdata[0]` entirely, or tag `ReceivedOutput`s with a maturity/confirmed-at field and make `SignableTransaction::new` reject inputs below COINBASE_MATURITY. The processor-side caller already hand-rolls the fix (`processor/src/networks/bitcoin.rs` scans `block.txdata[1 ..]` "which is burdened by maturity"), which confirms the intent; the library itself should enforce it rather than relying on each caller to post-process.

### Proof of Concept
```rust
// Conceptual: a miner crafts a coinbase paying to the vault script.
let key = /* even vault group key */;
let vault_script = p2tr_script_buf(key).unwrap();

let mut block: Block = /* block whose txdata[0] is a coinbase with
    TxOut { value, script_pubkey: vault_script } */;

let scanner = Scanner::new(key).unwrap();
let outputs = scanner.scan_block(&block);
// outputs contains a ReceivedOutput for the coinbase output
assert_eq!(outputs.len(), 1);

// The output is accepted as a spendable input:
let tx = SignableTransaction::new(
    outputs, &payments, change, None, fee_per_vbyte,
).unwrap();
// Signing succeeds and yields a fully-signed transaction that the
// network rejects (premature coinbase spend) — funds locked until
// maturity, with no indication to the caller.
```