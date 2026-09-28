### Title
Duplicate inputs double-counted in `SignableTransaction::new` produce a consensus-invalid transaction that locks funds — ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The Rio bug class is "available balance counted without subtracting already-committed amounts, so a request is accepted that cannot be settled." In `networks/bitcoin/src/wallet/send.rs`, `SignableTransaction::new` sums input values and builds `TxIn`s directly from the supplied `Vec<ReceivedOutput>` without checking for duplicate outpoints. An attacker who feeds `ReceivedOutput::read` bytes containing the same legitimate output twice causes the transaction to be funded only once on-chain while being accounted for twice — the threshold signs a transaction Bitcoin consensus rejects (duplicate inputs are invalid), so the payment never executes even though the protocol believed it had sufficient funds.

### Finding Description
`SignableTransaction::new` computes the spendable balance by summing every element of `inputs`:

```rust
// networks/bitcoin/src/wallet/send.rs L175
let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
```

and then maps every element into a `TxIn` keyed by `input.outpoint` with no deduplication (L177-185). `input_sat` is the sole arbiter of affordability:

```rust
// networks/bitcoin/src/wallet/send.rs L215
if input_sat < (payment_sat + needed_fee) {
  Err(TransactionError::NotEnoughFunds { ... })?;
}
```

If the same `ReceivedOutput` appears twice, `input_sat` counts its value twice while only one real UTXO exists. The resulting `Transaction` contains two inputs with identical `previous_output`, which `CheckTransaction` (and every mempool/consensus path) rejects as a double-spend within one transaction.

`ReceivedOutput::read` (networks/bitcoin/src/wallet/mod.rs L122-134) parses `offset`, `output`, and `outpoint` from raw bytes with no cross-field validation and no uniqueness tracking — a duplicated copy of a genuine scanned output deserializes cleanly. The only downstream check is in `multisig` (send.rs L273-285), which verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` per input. A duplicated legitimate output passes this check trivially, so `TransactionMachine::sign` proceeds and each participant produces signature shares for both duplicate inputs (send.rs L383-391), and `complete` assembles a fully-signed, permanently invalid transaction.

This mirrors Rio exactly: `getTotalBalanceForAsset` counted shares already queued for withdrawal as available, so `requestWithdrawal` admitted requests that could never be settled in `rebalance`. Here, already-"spent" input value is counted as available, so the signer admits a payment that can never settle on-chain.

### Impact Explanation
An unprivileged participant supplying the `ReceivedOutput` list (or a peer able to inject serialized `ReceivedOutput` bytes into the input-selection path) can make the threshold sign a transaction that can never confirm. The signing session is consumed, the intended payments are not made, and — as in the Rio report — the funds sit unspent until an honest corrected transaction is constructed. If the flow retries automatically with the same poisoned input set, the stall is persistent. No secret material leaks, so this is a Medium-severity liveness/funds-locking issue, matching the source finding's severity.

### Likelihood Explanation
Exploitation requires the attacker to influence the `inputs` vector — i.e., the same trust boundary the audit's rules grant for `ReceivedOutput::read` bytes. Duplicating a real output is trivial (copy the serialized bytes) and survives every existing validation, since each input is validated independently against the offset-derived script. No protocol-level dedup exists anywhere between `scan_transaction`/`read` and `SignableTransaction::new`.

### Recommendation
Reject duplicate outpoints in `SignableTransaction::new` (e.g., insert `input.outpoint` into a `HashSet` while iterating and error on re-insertion), and optionally verify in `ReceivedOutput::read` consumers that the outpoint's `script_pubkey` equals `p2tr_script_buf(offset.group_key())` before the input reaches transaction construction.

### Proof of Concept
```rust
// A received output scanned from the chain (or deserialized via ReceivedOutput::read)
let real: ReceivedOutput = scanned[0].clone();

// Duplicate it
let inputs = vec![real.clone(), real.clone()]; // same outpoint, value counted twice

// Suppose real.value = 60_000, payment = 100_000, fee = 1_000.
// input_sat = 120_000 >= 101_000 → passes NotEnoughFunds check,
// yet only 60_000 sats of real UTXO exists.
let stx = SignableTransaction::new(inputs, &payments, None, None, fee_rate).unwrap();

// multisig() succeeds: each duplicate's script matches its offset key.
let machine = stx.multisig(&keys).unwrap();
// ... threshold signing completes ...
let tx = signature_machine.complete(shares).unwrap();
// tx.input[0].previous_output == tx.input[1].previous_output
// → rejected by Bitcoin consensus (bad-txns-inputs-duplicate);
// payment never occurs despite the fully valid FROST signature.
```

Note on confidence: this analog assumes the input list can be attacker-influenced via the `ReceivedOutput::read` untrusted-bytes path as permitted by the scope rules. If `inputs` are guaranteed to come only from the local `Scanner` against already-verified chain data, duplicates cannot arise naturally and the finding reduces to a defense-in-depth gap rather than a reachable vulnerability.