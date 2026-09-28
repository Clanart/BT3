### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable `ReceivedOutput`s - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
The audit finding is “value can be received to an address/contract while the code provides no usable path to move it out.” In Serai’s in-scope Bitcoin wallet code, `Scanner::scan_block` scans `block.txdata` including `block.txdata[0]`, the coinbase transaction, and emits `ReceivedOutput` values described by the type as “A spendable output.” A coinbase output matching a registered script is therefore reported as received/spendable even though Bitcoin consensus forbids spending it until maturity. `Processor` code later skips coinbase in `get_outputs`, which confirms the production intent, but the in-scope wallet API still exposes the bad path.

### Finding Description
`Scanner::scan_transaction` matches outputs only by `output.script_pubkey` against `self.scripts` and returns `ReceivedOutput` for every match, without checking whether the containing transaction is coinbase. `Scanner::scan_block` then iterates `for tx in &block.txdata` and extends results with every transaction, so the coinbase is included. A miner or anyone able to cause a coinbase payout to a Serai P2TR script can make an unspendable-for-100-blocks output appear as a normal received output. Downstream `SignableTransaction::new` accepts arbitrary `ReceivedOutput`s and builds inputs from their outpoints, so an immature coinbase can be selected for spending.

### Impact Explanation
The scanner reports funds as received that are not currently spendable. If consumed, the constructed/signed transaction spends an immature coinbase and is consensus-invalid (`bad-txns-premature-spend-of-coinbase`), causing temporary fund lockup/DoS of that input and potentially stalling wallet flows that trust `ReceivedOutput` as spendable. This matches “funds reported received that are not spendable,” though the lock is bounded by coinbase maturity rather than permanent.

### Likelihood Explanation
Reachability requires a block whose coinbase pays to a scanned Serai script. That is not attacker-free, but it is a public on-chain input path: `scan_block` is explicitly fed untrusted block data, and miners/payout configurations can create coinbase outputs to arbitrary scripts. The processor-side Bitcoin integration avoids this by scanning `block.txdata[1 ..]`, but the wallet `Scanner` API itself remains unsafe for callers that use `scan_block` as documented.

### Recommendation
Change `Scanner::scan_block` to skip `block.txdata[0]` by default, or mark `ReceivedOutput` with coinbase/maturity metadata and have `SignableTransaction::new` reject immature coinbase inputs. At minimum, stop documenting `ReceivedOutput` as unconditionally spendable while `scan_block` can return immature coinbase outputs.

### Proof of Concept
- Register a scanner for an even key `K`; `Scanner::new(K)` inserts the base P2TR script with offset `0` in `networks/bitcoin/src/wallet/mod.rs`.
- Mine/create a regtest block whose coinbase `txdata[0]` pays `p2tr_script_buf(K)`; no private key material is needed.
- Call `Scanner::scan_block(&block)`; it iterates all `block.txdata`, including coinbase, and returns a `ReceivedOutput` for vout `0`.
- Pass that `ReceivedOutput` to `SignableTransaction::new`; it becomes a `TxIn` spending `OutPoint{txid: coinbase_txid, vout: 0}`.
- Broadcast fails consensus because the coinbase is immature, despite Serai reporting it as a spendable received output.