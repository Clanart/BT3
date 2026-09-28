### Title
`Scanner` reports Serai's own change outputs as newly received deposits, crediting the total output value rather than the net delta received - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The Tokemak bug credited `tokenInBalanceAfter` — the vault's *total* balance including pre-existing funds — instead of `tokenInBalanceAfter - tokenInBalanceBefore`, the amount actually received. Serai's Bitcoin wallet has the same shape: `Scanner::scan_transaction` reports every transaction output whose `script_pubkey` matches a registered script as a fresh `ReceivedOutput`, with no distinction between funds arriving from an external depositor and funds Serai already owned that are merely returning as change from an outgoing `SignableTransaction`.

### Finding Description
`Scanner` keys detection purely on `script_pubkey`:

```rust
// networks/bitcoin/src/wallet/mod.rs
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, output: output.clone(), ... });
}
```

`register_offset` inserts `(p2tr_script_buf(key + G*offset) -> offset)` pairs into `scripts`. Serai's own spend path, `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs`, creates a change output paying back to a Serai script:

```rust
// networks/bitcoin/src/wallet/send.rs
tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
```

Once Serai constructs any withdrawal/burn transaction, the change output is an output paying a Serai-controlled script in a transaction on the public chain. When `scan_transaction` / `scan_block` later process that transaction (necessarily, since scanners process every tx in every block), the change output matches `scripts` and is emitted as a `ReceivedOutput` — indistinguishable from a genuine deposit. Value that Serai already owned (the input prevouts being spent, minus payments and fee) is reported as *newly received*, exactly the `tokenInBalanceAfter`-vs-delta error: the full recycled balance is credited rather than the amount that actually came in.

A second facet: any registered offset script is a fixed, reusable address. A registered change/deposit script observed on-chain lets any unprivileged party pay it — that part is intended (it's a deposit address). The unintended part is that Serai's *own* transactions recycle value through the same registered script and are counted again.

### Impact Explanation
Each `ReceivedOutput` is the unit the downstream pipeline uses to account for received funds. Re-reporting change as a deposit means funds are credited twice: once when originally received, and again when Serai's outgoing transaction returns the remainder to a registered script. On the Serai side this translates to minting/accounting for coins that were never newly deposited — an unbacked credit that grows with every spend (each spend generates a fresh change output that gets re-credited). This mirrors the report's "incorrect rebalance" impact: the protocol acts on a larger amount of funds than were actually received.

### Likelihood Explanation
Triggering requires only that Serai produce a spend (any withdrawal batch) while a change script is among the registered offsets — a normal operating condition, not an edge case. No malicious validator, key compromise, or collusion is needed; the misclassification is deterministic given the script-only matching in `scan_transaction`.

### Recommendation
Distinguish genuinely incoming funds from recycled value. Options:

- Track spent outpoints / known self-produced txids and exclude outputs of transactions that spend `ReceivedOutput`s already owned by the wallet (i.e., compute the delta: outputs minus outputs funded by own inputs), or
- Use a dedicated change offset/script registered in a separate map so change outputs are recognized as internal movements, not deposits, or
- At minimum, document that consumers of `scan_block`/`scan_transaction` must post-filter outputs belonging to transactions Serai itself created, as is already done for coinbase maturity (the existing `scan_block` comment shows this pattern is accepted practice).

### Proof of Concept
1. Register offset `o` via `Scanner::register_offset`; the script `S = p2tr_script_buf(key + G*o)` enters `scripts`.
2. Attacker (or normal flow) deposits to `S`; `scan_transaction` yields `ReceivedOutput{offset: o, value: V}` — correctly credited.
3. Serai builds `SignableTransaction::new(inputs=[that output], payments=[...], change=Some(S_or_another_registered_script), ...)`, signs it via `TransactionMachine`/`TransactionSignatureMachine`, and broadcasts.
4. On the next block scan, `scan_block` encounters Serai's own transaction. The change output's `script_pubkey` equals a registered script, so a new `ReceivedOutput{offset, value: change_value}` is emitted.
5. The scanner reports `change_value` as newly received even though it is Serai's own pre-existing balance returning — the "total balance" is credited rather than the delta received, yielding an unbacked credit of up to `sum(inputs) - payments - fee` per transaction, repeatable on every subsequent spend.

Note: I could verify the scanner-side misclassification entirely within in-scope code (`networks/bitcoin/src/wallet/mod.rs`, `send.rs`); the exact crediting consequence is realized in the processor crate (out of scope), which I could not fully read — but `ReceivedOutput` emission of self-change is provable from the in-scope code alone and is the direct analog of the delta-vs-total-balance bug class.