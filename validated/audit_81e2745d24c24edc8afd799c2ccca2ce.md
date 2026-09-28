### Title
Scanner permanently credits coinbase and unspendable outputs with no eviction or maturity tracking - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The external report's bug class is *an entitlement that should expire (a lock past its end) remains valid forever, because nothing can evict it*. In `bitcoin_serai::wallet`, `Scanner::scan_block` and `Scanner::scan_transaction` emit `ReceivedOutput`s purely on `script_pubkey` matching, with no concept of validity, maturity, or spendability. Two consequences: (1) `scan_block` explicitly scans `block.txdata[0]` — the coinbase — so outputs paying the multisig are reported as received while Bitcoin consensus makes them unspendable for 100 blocks; (2) the `Scanner`/`ReceivedOutput` API has no spent/maturity/dust state at all, so once an output is reported, nothing in the wallet layer distinguishes a live UTXO from an immature or economically unspendable one.

### Finding Description
`Scanner` is defined at `networks/bitcoin/src/wallet/mod.rs:153-166` as `{ key, scripts: HashMap<ScriptBuf, Scalar> }`. `scan_transaction` (mod.rs:199-214) pushes a `ReceivedOutput` for every tx output whose `script_pubkey` is a registered script, keyed only on that match. `scan_block` (mod.rs:221-227) iterates `for tx in &block.txdata`, which includes `txdata[0]`, the coinbase. The only mitigation is a doc comment at mod.rs:217-219: *"This will also scan the coinbase transaction which is bound by maturity… a post-processing pass is needed to remove those outputs"* — i.e., the type emits immature outputs indistinguishably from spendable ones and provides no `spent`/`mature` flag, no removal method, and no per-output height to let a caller even compute maturity without re-deriving it externally.

Mirroring the report's "no kick function" root cause: there is no `mark_spent`, no `seen` set, no dust threshold, and no maturity check inside the wallet crate itself. The downstream processor compensates externally — `processor/src/networks/bitcoin.rs:691` skips `block.txdata[1 ..]`'s prefix (i.e., skips the coinbase) and filters `output.balance().amount.0 >= N::DUST` at `processor/src/multisigs/scanner.rs:564` — but those mitigations live outside `bitcoin_serai`, which is the in-scope library integrators consume. Any caller of `Scanner::scan_block` alone receives coinbase outputs as `ReceivedOutput` with value and outpoint fully populated (as the test at `networks/bitcoin/tests/wallet.rs:63-70` confirms: it scans the coinbase at `block.txdata[0]` and asserts it is returned), and `SignableTransaction`/`multisig` will happily build signing machines over whatever `ReceivedOutput`s are passed to it (`send.rs:273-285`), since the only check is `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — never spendability.

### Impact Explanation
High on the accepted-impact rubric: *funds reported received that are not spendable*. An unprivileged miner or counterparty who routes a coinbase payout (or a sub-dust/uneconomical output) to a Serai multisig script causes the wallet layer to report balance that cannot be moved — coinbase outputs for 100 blocks by consensus rule, dust outputs effectively forever given `SignableTransaction::new`'s fee/change math. Like the expired-lock points that keep accruing rewards, these outputs keep accruing "received" weight with no in-crate mechanism to evict or flag them.

### Likelihood Explanation
Fully reachable by an unprivileged party with a public input: sending a Bitcoin transaction paying to the multisig's P2TR script (`p2tr_script_buf(key)` at send.rs:277) is enough to mint a `ReceivedOutput`. Coinbase outputs to the address occur naturally whenever the address is used for mining payouts or in batched pools. Exploitation requires no malicious validator, leaked key, or broken primitive — only the absence of a validity/expiry check, exactly the reported bug class. Caveat: the severity is bounded by integrator behavior; the code documents the maturity caveat, so a reader could argue documented-misuse. The stronger, undocumented part is that `ReceivedOutput` carries no maturity/spent metadata at all, making correct handling impossible from the API alone without out-of-band bookkeeping.

### Recommendation
Give `ReceivedOutput` explicit spendability state: record the block height at scan time, expose `is_mature()`/`is_spendable(height)`, and have `scan_block` tag coinbase-derived outputs (check `tx.is_coinbase()` / `block.txdata[0]`) instead of treating them identically. Add a dust floor inside `Scanner` (the crate already defines `DUST` in `wallet`) so outputs below it are either rejected or flagged, and provide a `mark_spent`/removal API so spent or invalid outputs stop being reported as balance — the "kick" the report's recommendation calls for.

### Proof of Concept
```rust
// networks/bitcoin/tests/wallet.rs already demonstrates the primitive:
let block = rpc.get_block(&rpc.get_block_hash(block_number).await.unwrap()).await.unwrap();
let outputs = scanner.scan_block(&block);
// outputs[0] is the coinbase output at block.txdata[0] — reported received,
// yet consensus-unspendable for 100 blocks, and ReceivedOutput carries no flag.
assert_eq!(outputs[0].outpoint(), &OutPoint::new(block.txdata[0].compute_txid(), 0));
```
A PoC beyond this (spending attempt rejected by the mempool, or dust-output accounting) would require a regtest harness; the semantic defect — `scan_block` returning `txdata[0]` outputs as ordinary `ReceivedOutput`s with no maturity or spent tracking — is directly visible at `networks/bitcoin/src/wallet/mod.rs:221-227`.