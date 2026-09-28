### Title
Scanner reports economically unspendable dust outputs as received funds with no minimum-value bound - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` matches incoming outputs solely on `script_pubkey` and returns a `ReceivedOutput` for every match, regardless of the output's value. An arbitrary third party can send a 1-satoshi (or otherwise dust) output to any registered Serai script, and it will be reported as a received, spendable wallet output even though spending it costs more in fees than it is worth — the analog of executing a swap with no minimum-output bound.

### Finding Description
The KyberSwap report describes execution proceeding with no lower bound on what the user actually receives. In Serai, the equivalent "no lower bound on received value" lives in the Bitcoin output scanner. `scan_transaction` at `networks/bitcoin/src/wallet/mod.rs:199-214` iterates over `tx.output`, looks up `output.script_pubkey` in `self.scripts`, and pushes a `ReceivedOutput` unconditionally — there is no check on `output.value`.

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput {
    offset: *offset,
    output: output.clone(),
    outpoint: OutPoint::new(tx.compute_txid(), vout),
  });
}
```

`ReceivedOutput`s feed `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`), which treats each input's `output.value` as usable funds. A Taproot input costs ~57 vbytes to spend (per the in-file commentary, `networks/bitcoin/src/wallet/mod.rs` / `send.rs` weight accounting: `(4*(36+1+4)) + 66 = 230 WU ≈ 57 vbytes`). At even the minimum relay fee of 1 sat/vbyte, an input under ~57 satoshis is a net liability; at realistic fee rates the spendable threshold is hundreds of satoshis. Bitcoin Core itself treats Taproot outputs below ~330 sats as dust. The scanner reports all of them as received.

Notably, the higher-level consumer in `processor/src/multisigs/scanner.rs:564` already applies `output.balance().amount.0 >= N::DUST` (10 000 sats for Bitcoin, `processor/src/networks/bitcoin.rs:638`), acknowledging that sub-threshold outputs are not spendable — but `bitcoin-serai`'s `Scanner` itself performs no such bound, so any consumer relying on `scan_transaction` / `scan_block` (or `ReceivedOutput::read`-fed data) receives outputs it can never profitably spend.

### Impact Explanation
An unprivileged party with only the ability to send Bitcoin transactions can pollute the wallet's view with dust outputs that are reported as received funds but are economically unspendable (spending them costs more than their value). This inflates reported balances, and if the wallet's coin selection includes such inputs, the transaction either silently burns value (the dust input contributes less than its marginal fee, so the signer pays the difference) or, when `change` is `None`, the shortfall is burned entirely as fee — producing a signed transaction that spends more than intended, i.e., signing of an economically unintended message.

### Likelihood Explanation
Requires only that an attacker know (or observe on-chain) a registered Serai script and broadcast a dust payment to it — fully within the "Bitcoin transactions they send" reachability rule. Cost is a dust output plus one transaction fee. No privileged access, collusion, or leaked secrets needed.

### Recommendation
Enforce a minimum-value bound in `scan_transaction`/`scan_block` (e.g., a `DUST`/minimum-spendable constant commensurate with the ~57-vbyte input spend cost), or expose the bound on `ReceivedOutput`/`SignableTransaction` so sub-threshold outputs are excluded from reporting and from coin selection by default.

### Proof of Concept
```rust
// Attacker learns a registered script (public on-chain).
let mut scanner = Scanner::new(vault_key).unwrap();
let dust_offset = scanner.register_offset(offset).unwrap();
let dust_script = p2tr_script_buf(vault_key + G * dust_offset).unwrap();

// Attacker broadcasts a TX paying 1 satoshi to dust_script.
let tx: Transaction = /* tx with TxOut { value: Amount::from_sat(1), script_pubkey: dust_script } */;

// Scanner reports it as a received output despite it being unspendable:
let outputs = scanner.scan_transaction(&tx);
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].output.value.to_sat(), 1); // spending it costs ~57+ sats in fees
```
Supporting code: `Scanner::scan_transaction` lacks any value check (`networks/bitcoin/src/wallet/mod.rs:205-211`), while `SignableTransaction::new` sums all provided input values as usable funds (`networks/bitcoin/src/wallet/send.rs:175`), and the ~57-vbyte per-input cost is documented in `processor/src/networks/bitcoin.rs:619-620`.