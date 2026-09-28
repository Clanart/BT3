### Title
Scanner reports economically unspendable dust outputs as received spendable funds — (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` (and `scan_block`) reports any on-chain output whose `script_pubkey` matches a registered key/offset as a `ReceivedOutput` — documented in the code as "A spendable output" — without checking that the output's value can cover the fee required to spend it. An unprivileged attacker can send a valid Bitcoin transaction creating arbitrarily many dust-valued outputs (e.g., 1 satoshi, or a bare P2TR output of 330 sats) to the multisig's deposit address or any registered offset address, all of which are deterministically derivable public values. The scanner reports them as received and spendable even though each such input costs more in fees (~57 vbytes per Taproot input) than it contributes, and a transaction built solely from them cannot pay its own fee.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`, `scan_transaction` iterates `tx.output`, looks up `self.scripts.get(&output.script_pubkey)`, and pushes a `ReceivedOutput { offset, output, outpoint }` for every match. There is no check on `output.value`, no check that the outpoint is still unspent later in the same block, and no check that the output is economically spendable. The struct is explicitly documented as "A spendable output" (line 88), yet nothing enforces that invariant on the receive path. This mirrors the reported bug class exactly: the receive handler (`onERC721Received` / `scan_transaction`) marks an asset as received based purely on a caller-controlled event (the NFT "transfer" callback / a matching `script_pubkey`), without verifying the received asset actually satisfies the conditions required for it to be usable. The downstream spending path (`SignableTransaction::new` in `wallet/send.rs`, which consumes `Vec<ReceivedOutput>` and returns `NotEnoughFunds`/`NoOutputs` errors) assumes inputs are worth spending; dust inputs either make the transaction unbuildable or silently absorb value as fees.

### Impact Explanation
An attacker can permanently pollute a Serai Bitcoin multisig's scanned UTXO set with dust outputs. Each is reported to the integrator as received, spendable funds. When the wallet later attempts to spend them, either (a) the dust inputs are included and consume more fee than their value — a direct, ongoing siphoning of the multisig's balance proportional to attacker spend (the attacker only pays dust + one-time tx fee, while the multisig pays ~57+ vbytes per polluted input at spend time), or (b) a plan built only on dust inputs fails with `NotEnoughFunds` after being reported received — funds reported received that are not spendable. This matches the accepted impact class "funds reported received that are not spendable."

### Likelihood Explanation
The attack requires only the ability to send a standard Bitcoin transaction to a publicly known/derivable address — the deposit address is public by design, and the offset-derived scripts (`register_offset` inserts script→offset pairs keyed purely on script) are computable from the public group key. The cost is minimal: one transaction can create hundreds of dust outputs. Any wallet or processor consuming `Scanner` output without an external dust filter (the crate itself defines no minimum-value invariant on `ReceivedOutput`) is affected.

### Recommendation
Enforce a minimum-value check inside `Scanner::scan_transaction`/`scan_block` (or at `ReceivedOutput` construction), refusing to report outputs whose value is below a defined spendability threshold (e.g., the input's marginal fee cost at a conservative feerate multiplied by a safety margin). Additionally, when scanning a full block, discard matches whose outpoint is spent by a later transaction in the same block, so only actually-unspent outputs are reported.

### Proof of Concept
```rust
// Attacker crafts one transaction paying `p2tr_script_buf(victim_key)`
// (or any offset script from scanner.register_offset) N outputs of 330 sats
// each — the consensus-minimum non-dust-relay value for P2TR.
//
// networks/bitcoin/src/wallet/mod.rs
let outputs = scanner.scan_transaction(&attacker_tx);
// outputs.len() == N; every ReceivedOutput.value() == 330
// Each is reported as spendable, yet spending it costs ~57 vbytes * feerate
// (>330 sats at even ~6 sat/vbyte), and SignableTransaction::new built only
// from these inputs errors with NotEnoughFunds — funds "received" but not
// spendable.
```
The missing guard is at `scan_transaction` lines 199–214: the only predicate is `self.scripts.get(&output.script_pubkey)`, with no `output.value` floor and no same-block spend check, despite `ReceivedOutput` being documented as "A spendable output."

Note on scope/confidence: the concrete fund-loss path (scheduler ingesting these outputs and constructing fee-negative plans) lives in `processor/` (out of scope), but the root cause — reporting received outputs without verifying spendability — is entirely within in-scope `networks/bitcoin/src/wallet/mod.rs`. I did not fully verify whether `send.rs`'s `SignableTransaction::new` filters dust inputs internally; if it does, the impact reduces to unspendable-funds-reporting rather than fee drain, but the reported-received-not-spendable analog stands either way.