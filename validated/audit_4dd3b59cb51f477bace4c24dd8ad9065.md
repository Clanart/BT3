### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to CVE-2020-16163 — where RRDP fetches proceeded without the required validation of the endpoint — `Scanner::scan_block` proceeds to emit `ReceivedOutput`s for the coinbase transaction without validating coinbase maturity. A coinbase output is unspendable for 100 blocks, yet the scanner reports it identically to a normal, immediately spendable output. The processor-level code (`get_outputs` in `processor/src/networks/bitcoin.rs`) had to add an explicit workaround (`block.txdata[1 ..]`), confirming the library function itself omits the validation.

### Finding Description
`Scanner::scan_transaction` reports any output whose `script_pubkey` matches a registered script (`wallet/mod.rs:199-214`), and `scan_block` applies it to *every* transaction in the block, including `block.txdata[0]` — the coinbase (`wallet/mod.rs:221-227`). A `ReceivedOutput` claims to be "A spendable output" (`wallet/mod.rs:88`), yet a coinbase output is consensus-unspendable for 100 confirmations. The only guard is a doc comment telling callers to post-filter — the function proceeds without performing the check, the same "processing proceeds despite missing validation" shape as the reference CVE. Downstream, Serai's own Bitcoin network layer acknowledges the missing check: `get_outputs` explicitly comments "Skip the coinbase transaction which is burdened by maturity" and iterates `block.txdata[1 ..]` (`processor/src/networks/bitcoin.rs:690-691`), meaning any consumer that does not replicate this workaround (e.g., a caller scanning a single block, or a coinbase-like maturity rule on a fork) is fed unspendable funds as spendable.

### Impact Explanation
Any consumer of `scan_block` that treats the returned `ReceivedOutput`s as spendable will credit funds that cannot actually be spent. If such outputs are fed into `SignableTransaction::new` / `multisig`, the resulting transaction is consensus-invalid (immature coinbase spend) and rejected by the network — causing signing rounds to be wasted on a transaction that can never confirm, or accounting that credits a deposit which is not yet real. On regtest/test flows and any non-processor integration, this is exactly "funds reported received that are not spendable."

### Likelihood Explanation
Reachable by any unprivileged party: miners (or on regtest, anyone) can direct a coinbase payout to the Serai multisig's P2TR script (e.g., `generatetoaddress` to the group address, as Serai's own test does at `networks/bitcoin/tests/wallet.rs:43-52` — where the test only passes because it additionally mines 100 blocks and the assertion would fail against an immature block scanned by a naive consumer). Whether impact materializes depends on the consumer not re-implementing the maturity filter; the library contract ("A spendable output") is violated regardless.

### Recommendation
In `Scanner::scan_block` (`networks/bitcoin/src/wallet/mod.rs:221`), skip `block.txdata[0]` or explicitly mark returned coinbase outputs as immature (e.g., carry a `maturity`/`is_coinbase` field on `ReceivedOutput` set from `tx.is_coinbase()`), so the "spendable output" invariant holds without requiring every caller to duplicate the check that `processor/src/networks/bitcoin.rs:690-691` already had to add.

### Proof of Concept
```rust
// networks/bitcoin context; regtest node via Rpc
let key = /* even group key */;
let scanner = Scanner::new(key).unwrap();

// Mine a single block paying the coinbase to Serai's P2TR script
let block_number = rpc.get_latest_block_number().await.unwrap() + 1;
rpc.rpc_call::<Vec<String>>(
    "generatetoaddress",
    serde_json::json!([1, Address::from_script(&p2tr_script_buf(key).unwrap(),
                                                   Network::Regtest).unwrap()]),
).await.unwrap();

let block = rpc.get_block(&rpc.get_block_hash(block_number).await.unwrap()).await.unwrap();

// scan_block returns the coinbase output as a ReceivedOutput ("a spendable output")
let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1); // reported as spendable

// Yet this output is consensus-unspendable for 100 blocks:
assert!(block.txdata[0].is_coinbase());
// Feeding `outputs[0]` into SignableTransaction::new + multisig + complete produces a
// transaction that bitcoind rejects with "bad-txns-premature-spend-of-coinbase".
```