### Title
Coinbase outputs reported as spendable received funds despite being immature — funds accounted but not spendable - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_block` returns `ReceivedOutput`s for every transaction in a block, including the coinbase transaction. Bitcoin consensus makes coinbase outputs unspendable for 100 blocks, yet the scanner reports them identically to normal outputs — value is credited as received while the output cannot actually be spent. This mirrors the reported bug class: an amount is accounted for on the balance side (deducted from the price / reported as received) while the corresponding transfer-side condition silently fails, and the result is returned as success regardless.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `scan_transaction` matches `output.script_pubkey` against registered scripts and unconditionally emits a `ReceivedOutput` (lines 199–214). `scan_block` (lines 221–227) iterates `block.txdata` starting at index 0, i.e. it includes the coinbase transaction, whose outputs are immature for `COINBASE_MATURITY` (100) blocks per consensus. The code's own doc comment concedes the defect: "This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed." No such filtering is performed inside the in-scope crate — `ReceivedOutput` carries only `offset`, `output`, and `outpoint` (lines 90–97), so downstream consumers such as `SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150) have no marker distinguishing immature from spendable inputs and will happily construct a transaction spending a coinbase outpoint, which the network will reject.

Additionally, `scan_transaction` only checks `script_pubkey` equality; it never validates that the P2TR output key actually derives from `self.key + offset*G` beyond the script mapping — but more critically for this class, it returns `Vec<ReceivedOutput>` (analogous to the `result` flag initialized `true`): every matched output is reported as a valid receipt even when the output is consensus-unspendable.

### Impact Explanation
An attacker who mines a block can point a coinbase output at the Serai multisig's `p2tr_script_buf` scriptPubKey. The scanner then credits those funds as received. Any downstream logic building a `SignableTransaction` over that `ReceivedOutput` produces a transaction that is invalid by consensus (immature coinbase spend), so the credited value is not spendable — the same "subtracted/credited but never transferred" loss shape as the royalty bug. If such an output is consumed as an input alongside legitimate ones, the entire spend fails, and the falsely-credited balance can corrupt fee/change accounting (`input_sat` overstates spendable funds in `SignableTransaction::new`, send.rs:175–221).

### Likelihood Explanation
Triggering requires mining a Bitcoin block (or via a mining pool's coinbase construction), which is costly but does not require compromising any validator, key share, or peer — it is reachable purely through transaction data fed to `scan_block`, an explicitly in-scope input path. The impact (DoS of spends mixing the immature output, plus mis-accounted balance) is bounded but real; note the comment acknowledges the hazard and shifts responsibility to callers, and I could not confirm within the indexed scope whether the processor performs the required post-processing pass — if it does not, this is a live bug; if it does, it is a latent footgun at Medium severity.

### Recommendation
Have `scan_block` skip `block.txdata[0]` (or check `tx.is_coinbase()`) when producing `ReceivedOutput`s, or tag `ReceivedOutput` with a maturity marker so `SignableTransaction::new` can reject immature inputs. Alternatively, require callers to pass the block height and filter coinbase outputs younger than 100 confirmations inside the scanner rather than relying on an undocumented post-processing obligation.

### Proof of Concept
```rust
// networks/bitcoin — conceptual PoC
let scanner = Scanner::new(multisig_key).unwrap();
// Attacker mines a block whose coinbase pays to p2tr_script_buf(multisig_key)
let outputs = scanner.scan_block(&attacker_block);
// outputs[0] is the coinbase output — reported as a normal ReceivedOutput
assert_eq!(outputs[0].outpoint().txid, attacker_block.txdata[0].compute_txid());
// Building a spend over it succeeds in-crate...
let tx = SignableTransaction::new(outputs, &payments, None, None, fee).unwrap();
// ...yet the signed transaction is consensus-invalid: spending an immature coinbase
// (BIP-113/COINBASE_MATURITY = 100 blocks). Funds were reported received but are
// not spendable — value credited, transfer never deliverable.
```

Uncertainty note: I was unable to inspect `processor/src/networks/bitcoin.rs` coinbase handling within available iterations; whether upstream code performs the documented maturity post-processing determines if this is exploitable end-to-end or a latent scanner defect.