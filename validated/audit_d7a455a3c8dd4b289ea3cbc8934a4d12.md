### Title
DoS of withdrawals via dust-output spam filling the vault's spendable UTXO set - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The bug class is "attacker fills a bounded per-account structure via a permissionless public entry point, DoSing withdrawals." Serai's Bitcoin wallet has the same shape: anyone can send arbitrary Taproot outputs to the vault's script_pubkey. `Scanner` cannot distinguish attacker dust from legitimate deposits (it matches on `script_pubkey` only), so every dust output becomes a `ReceivedOutput` indistinguishable from real funds. When a withdrawal transaction is constructed with `SignableTransaction::new`, each received output becomes an input whose weight is checked against `MAX_STANDARD_TX_WEIGHT`; once enough dust UTXOs accumulate, every withdrawal attempt errors with `TooLargeTransaction`.

### Finding Description
`Scanner::scan_transaction` records any output whose `script_pubkey` is registered, returning a `ReceivedOutput` for each match (`networks/bitcoin/src/wallet/mod.rs:199-214`). The vault's base script is registered in `Scanner::new` (`mod.rs:162-166`), so any Bitcoin user can create UTXOs payable to it with no authorization.

`SignableTransaction::new` consumes `inputs: Vec<ReceivedOutput>` and builds one `TxIn` per output (`send.rs:175-185`), computes the aggregate weight via `calculate_weight_vbytes` (`send.rs:204-226`), and hard-fails:

```rust
if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT) {
  Err(TransactionError::TooLargeTransaction)?;
}
```
(`send.rs:241-243`)

Each Taproot input contributes ~57.5 vbytes (~230 WU) plus a 64-byte witness allowance, so roughly 1,700 dust inputs push any transaction over the 400,000 WU standardness cap. At the `DUST` minimum of 546 sats (`send.rs:32`), the attack costs under ~1 BTC total, and importantly the dust can be spread across many transactions over time. There is also a secondary effect: the per-input fee `needed_fee = fee_per_vbyte * vbytes` scales with input count (`send.rs:204-213`), so even before the weight cap, accumulated dust can trigger `NotEnoughFunds` when the dust inputs' value is less than the marginal fee to spend them — i.e., "funds reported received that are not spendable."

Unlike the original report, there is no alternative accounting path here: the library provides no dust filtering or input-selection logic inside `SignableTransaction::new`; whatever `ReceivedOutput`s the caller collected are all consumed as inputs.

### Impact Explanation
Griefing / DoS of withdrawals from a Bitcoin vault, matching the original report's impact classification. Every output created by an attacker to the vault's script_pubkey is indistinguishable from a legitimate deposit to `Scanner`, so downstream consumers that feed all scanned outputs into `SignableTransaction::new` will permanently fail to construct a standard transaction once the weight cap is exceeded. Funds remain recoverable only if callers implement their own input selection outside the library.

### Likelihood Explanation
Medium, mirroring the original assessment: the attack is permissionless (any Bitcoin user can send to a known Taproot address, no validator privileges needed) but carries a real cost (hundreds-to-thousands of ≥546-sat outputs plus fees). The vault address is publicly derivable from the group key via `p2tr_script_buf` (`mod.rs:80-86`), so no insider knowledge is required.

### Recommendation
Track the analog of Velodrome's "one point per block" fix: bound and filter what reaches the spend path. Concretely: (a) have the `Scanner`/consumer enforce a minimum-value threshold on `ReceivedOutput`s below which spending costs more in fees than the output's value, (b) support explicit input selection / UTXO consolidation in `SignableTransaction::new` rather than consuming the entire provided set, and (c) document that callers must not blindly feed all scanned outputs into a single transaction.

### Proof of Concept
Conceptual test (requires a regtest/tx-construction harness):

```rust
let scanner = Scanner::new(group_key).unwrap();
let vault_script = p2tr_script_buf(group_key).unwrap();

// Attacker floods the vault with dust
let mut inputs = vec![];
for i in 0..2000u32 {
    // tx: anyone-can-build tx paying `vault_script` 546 sats
    let tx = build_tx_paying(&vault_script, 546);
    inputs.extend(scanner.scan_transaction(&tx));
}

// Withdrawal attempt: feed all scanned outputs
let res = SignableTransaction::new(
    inputs,
    &[(payment_script, 10_000)],
    Some(change_script),
    None,
    fee_per_vbyte,
);
// Errors with TransactionError::TooLargeTransaction once
// weight > MAX_STANDARD_TX_WEIGHT (send.rs:241)
assert!(matches!(res, Err(TransactionError::TooLargeTransaction)));
```

Caveat: the exact input count at which `TooLargeTransaction` trips depends on the per-input weight in `calculate_weight_vbytes` (`send.rs:62-127`), and the mitigation of dropping dust inputs exists only outside the library — `SignableTransaction::new` itself performs no filtering.