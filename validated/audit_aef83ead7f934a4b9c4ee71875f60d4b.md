### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to `_mint` delivering an NFT to a recipient that cannot handle it, `Scanner::scan_block` delivers a Bitcoin output to the wallet layer that the wallet cannot actually spend: outputs contained in the block's coinbase transaction are bound by Bitcoin's 100-block maturity rule, yet they are returned as ordinary spendable `ReceivedOutput`s indistinguishable from mature outputs.

### Finding Description
`Scanner::scan_block` iterates over `block.txdata` in its entirety, including `txdata[0]` (the coinbase transaction), and forwards every output whose `script_pubkey` matches a registered script into a `ReceivedOutput` via `scan_transaction` (networks/bitcoin/src/wallet/mod.rs:221-227, 199-214). A `ReceivedOutput` is documented as "A spendable output" and carries only `offset`, `output`, and `outpoint` — no flag marking coinbase provenance (lines 88-97). Any downstream consumer of `scan_block` (e.g., `SignableTransaction::new`, networks/bitcoin/src/wallet/send.rs:150-256) will treat such an output as immediately spendable input; a transaction built and signed spending it will be rejected by the network (`bad-txns-premature-spend-of-coinbase`), or worse, if the scanning consumer credits the balance without a maturity gate, the "received" funds are reported but cannot move for 100 blocks.

The reachability matches the report's threat model: an unprivileged party — any miner — can pay to the Serai multisig's P2TR script in a coinbase transaction. Unlike `_safeMint`, no check in the returned data or the scan API rejects or marks the unspendable recipient/asset. The function's doc comment acknowledges this ("This will also scan the coinbase transaction which is bound by maturity"), but `scan_block` itself provides no enforcement or metadata, and the only safe alternative requires callers to reimplement filtering (`block.txdata[1..]`).

### Impact Explanation
Funds reported received that are not spendable. A consumer crediting coinbase-paid outputs as available balance can build a `SignableTransaction` over an immature outpoint: `multisig`/`sign` will produce a validly-signed transaction (send.rs:273-285, 355-398) that every node rejects, and the wallet may additionally mis-account the balance as liquid for 100 blocks. In a threshold-setting this burns a full signing round on an unbroadcastable transaction and can stall or corrupt accounting of the multisig's UTXO set.

### Likelihood Explanation
Requires a miner to include a payment to the scanned P2TR script in the coinbase — a low-probability but permissionless action (any miner can construct it; merged-mining/donation payouts to arbitrary addresses in coinbases do occur). Once included, any caller using the documented `scan_block` API path hits the bug deterministically, since there is no maturity marker on `ReceivedOutput`. Impact is bounded (temporary unspendability + a wasted signing ceremony rather than permanent loss), consistent with Medium severity.

### Recommendation
In `Scanner::scan_block` (or `scan_transaction` when invoked on a coinbase), either skip `block.txdata[0]` entirely — matching the processor behavior of scanning `block.txdata[1..]` — or extend `ReceivedOutput` with a maturity/immaturity flag and reject its use in `SignableTransaction::new` until mature. At minimum, make the returned type distinguish coinbase outputs so callers cannot silently treat them as spendable.

### Proof of Concept
1. A miner mines a block whose coinbase (`block.txdata[0]`) contains `TxOut { value: v, script_pubkey: p2tr_script_buf(group_key) }`.
2. A wallet calls `scanner.scan_block(&block)`; `scan_transaction` matches the `script_pubkey` against `self.scripts` (lines 205-211) and returns `ReceivedOutput { offset: Scalar::ZERO, output, outpoint }`.
3. The wallet passes this `ReceivedOutput` to `SignableTransaction::new`, which accepts it — no maturity check exists anywhere in `new` (send.rs:150-256) — and `multisig()` succeeds because `p2tr_script_buf(key + offset) == prevout.script_pubkey` (send.rs:277).
4. The FROST signers complete the transaction; broadcast fails with `bad-txns-premature-spend-of-coinbase`, and the output was meanwhile accounted as spendable balance despite being immature.