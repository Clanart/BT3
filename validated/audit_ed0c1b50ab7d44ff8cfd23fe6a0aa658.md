### Title
Coinbase payments to Serai outputs are never reported, permanently stranding funds — ([File: processor/src/networks/bitcoin.rs])

### Summary
The Bitcoin network implementation scans blocks for outputs paying to the Serai multisig's keys, but unconditionally skips `block.txdata[0]` (the coinbase transaction) in `get_outputs`. Any miner or unprivileged party who pays to a Serai deposit/branch/forward script via a coinbase output creates a real, spendable UTXO that the scanner will never emit. The funds are never credited and, since no other code path ever revisits the output, they are permanently unclaimable by the protocol — the same shape as "revenue accumulates with no withdrawal mechanism."

### Finding Description
`Bitcoin::get_outputs` iterates `for tx in &block.txdata[1 ..]`, skipping index 0 to avoid coinbase-maturity burdened outputs (`processor/src/networks/bitcoin.rs:691`). The downstream `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:199`) happily matches coinbase outputs — `Scanner::scan_block` even documents that coinbase is scanned and maturity "bound" — but the processor drops them entirely rather than deferring them until maturity.

A coinbase transaction can pay to arbitrary script_pubkeys, including the P2TR scripts registered by `scanner()` for `OutputType::External` (offset ZERO), `Branch`, `Change`, and `Forwarded` (`processor/src/networks/bitcoin.rs:314-346`). Because outputs are identified by outpoint and the scanner only walks each block once, there is no later pass that would pick up the matured coinbase output. The UTXO exists on-chain, is spendable by the threshold key after 100 confirmations, yet no `ScannerEvent` is ever emitted for it, so the protocol never credits the depositor and never spends the output.

### Impact Explanation
Value sent to Serai via coinbase outputs is permanently stranded: the deposit is never credited and the output is never included in any `SignableTransaction` input set, so the protocol can never recover it. This is a permanent loss of funds reachable by an unprivileged party (any miner constructing a block, or anyone who can convince one to include such an output). Over time such outputs silently accumulate with no mechanism for utilization — directly analogous to the reported "protocol rewards permanently stuck."

### Likelihood Explanation
Low-to-moderate: it requires the payment to be embedded in a coinbase transaction, which means mining a block or paying a miner/pool to include the output. However, no protocol privilege, leaked key, or validator misbehavior is needed — only a Bitcoin transaction the attacker causes to exist. Once broadcast, the funds are irrevocably unrecoverable through the protocol's own code paths.

### Recommendation
Don't blindly skip `txdata[0]`. Either scan the coinbase transaction and queue its outputs behind a maturity check (the design `scan_block` already anticipates — see the comment at `networks/bitcoin/src/wallet/mod.rs:216-220` noting outputs "bound by maturity" need post-processing), or periodically re-scan matured coinbase outputs. A `presumed_origin`/maturity flag on `Output` would let the scheduler treat them as pending until 100 confirmations deep.

### Proof of Concept
1. Register/derive a Serai `External` output script via `Scanner::new(key)` / `p2tr_script_buf(key)`.
2. Mine a regtest block whose coinbase (`block.txdata[0]`) includes a `TxOut { value: >= DUST, script_pubkey: <that script> }`.
3. Call `Bitcoin::get_outputs(&block, key)` — the loop at `processor/src/networks/bitcoin.rs:691` starts at `txdata[1 ..]`, so the coinbase output is never yielded despite `scanner.scan_transaction` being able to match it (confirmed by `scan_block` including index 0 at `networks/bitcoin/src/wallet/mod.rs:221-227`).
4. No `ScannerEvent` is emitted; the output ID is never recorded in `ram_outputs`/seen-set, and no later block re-examines it. The funds remain spendable on-chain but permanently unclaimed by the protocol.