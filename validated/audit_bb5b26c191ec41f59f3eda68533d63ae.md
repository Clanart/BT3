### Title
Scanner reports immature coinbase outputs as spendable received funds - (networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner` in `networks/bitcoin/src/wallet/mod.rs` treats any block transaction output whose `script_pubkey` matches a registered address as a fully usable `ReceivedOutput`. `scan_block` iterates `block.txdata` starting at index 0, so it also scans the coinbase transaction. Coinbase outputs are unspendable for 100 blocks under Bitcoin consensus rules, yet they are returned indistinguishably from mature outputs, carrying a valid `outpoint` and `value()` that downstream accounting and `SignableTransaction::new` will consume as real balance.

### Finding Description
The external report's class is crediting a transfer of an untrusted/unverified asset without checking the actual resulting balance. The Serai analog lives in the scanner: `scan_transaction` (mod.rs:199-214) constructs a `ReceivedOutput` solely on a `script_pubkey` hash-map hit, and `scan_block` (mod.rs:221-227) feeds every transaction in `block.txdata` through it, including `txdata[0]` (the coinbase). Nothing marks the coinbase output as immature or excludes it.

Any miner — an unprivileged party — can craft a coinbase transaction paying to a multisig's P2TR script. The scanner will emit a `ReceivedOutput` whose `value()` contributes to `input_sat` in `SignableTransaction::new` (send.rs:175), passing the `NotEnoughFunds` check (send.rs:215). The resulting transaction commits to that input via `Prevouts::All` (send.rs:375) and produces valid FROST/Schnorr signatures, but the transaction is consensus-invalid until maturity, and the reported balance is not spendable.

A code comment at mod.rs:218-220 acknowledges the coinbase is scanned and says a post-processing pass is "needed" to remove such outputs, but the API itself performs no filtering, and there is no field on `ReceivedOutput` distinguishing an immature coinbase output from a spendable one — so any caller of `scan_block` gets unusable funds reported as received with no way to tell the difference from the `ReceivedOutput` alone.

### Impact Explanation
Funds are reported as received and counted toward spendable balance when they cannot actually be spent for 100 blocks. If the scanner output feeds UTXO selection directly, the multisig constructs, signs, and broadcasts a consensus-invalid transaction (coinbase-maturity violation), burning the signing session and potentially mis-accounting incoming funds. If the multisig's balance bookkeeping trusts `scan_block` results, deposits are credited that are temporarily unspendable, enabling accounting discrepancies. Impact: Medium — no permanent fund loss, but incorrect funds-received reporting and wasted/failed spends triggered by any miner.

### Likelihood Explanation
Reachable by any unprivileged party who can get a transaction into a block (miners, or any miner accepting a transaction paying the multisig script is unnecessary — the coinbase itself is attacker-controlled by definition of mining). Every block contains a coinbase, so any coinbase paying the watched script triggers it; mining pool payouts and solo miners routing rewards to arbitrary scripts make this realistic rather than exotic. Likelihood: Medium.

### Recommendation
In `scan_block`, skip `block.txdata[0]` (or accept a `maturity_height`/block-height parameter and mark/filter coinbase outputs), matching the alternative already hinted at in the comment. Additionally, add an `is_coinbase`/maturity flag to `ReceivedOutput` so callers cannot silently treat immature outputs as spendable, and have `SignableTransaction::new` reject or require confirmation depth metadata for inputs.

### Proof of Concept
1. Multisig creates `Scanner::new(group_key)`; address A = `p2tr_script_buf(key)`.
2. A miner includes a coinbase output paying `value` sats to A in block B at height h.
3. Processor calls `scanner.scan_block(&B)` → returns `ReceivedOutput { offset: 0, output: coinbase_txout, outpoint }`.
4. UTXO selection passes it to `SignableTransaction::new`; `input_sat >= payments + fee` check passes (send.rs:215); `multisig()` verifies `p2tr_script_buf(offset.group_key()) == prevout.script_pubkey` (send.rs:277) and proceeds — the script check cannot detect immaturity.
5. Threshold signing produces a valid Schnorr signature over `taproot_key_spend_signature_hash` (send.rs:386); the signed transaction is broadcast and rejected by all nodes: coinbase outputs are not spendable until height h+100 (BIP/consensus rule), so the "received" funds were never spendable.