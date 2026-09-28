### Title
SignableTransaction fee/weight accounting omits the OP_RETURN data output - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to CVE-2017-17880 — where a `WEBP_DECODER_ABI_VERSION` check gated which fields were populated, causing the encoder to read past the actually-initialized data — `SignableTransaction::new` conditionally appends an `OP_RETURN` output to `tx_outs`, but the subsequent weight/vsize/fee computation is run over `payments` only. The conditionally-added output is therefore never accounted for, so every data-bearing transaction is signed with a fee and a weight bound computed for a different (smaller) transaction than the one actually produced.

### Finding Description
In `SignableTransaction::new`, the data output is added before the fee calculation:

```rust
// networks/bitcoin/src/wallet/send.rs:194-204
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(
      PushBytesBuf::try_from(data).expect("data didn't fit into PushBytes depsite being checked"),
    ),
  })
}

let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` builds its mock transaction purely from `payments` plus an optional `change` script (`send.rs:85-99`); the `OP_RETURN` output present in `tx_outs` is absent from that mock. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` undercharges by ~10–100 vbytes (an `OP_RETURN` output carrying the maximum 80 allowed bytes is ~90 bytes ≈ 90 vbytes of weight the fee never covers).
2. When change exists, `change_value = input_sat - payment_sat - fee_with_change` uses the same undercharged `fee_with_change`, so the change output silently absorbs the shortfall (`send.rs:224-234`). The resulting transaction's *actual* fee rate is below the caller-specified `fee_per_vbyte`.
3. The `MAX_STANDARD_TX_WEIGHT` standardness check (`send.rs:241`) is evaluated on `weight` which excludes the data output's ~370 weight units, so a transaction can pass the check while the produced transaction is non-standard and rejected by relay policy.

The `data` is not purely internal: the processor constructs spends that embed `InInstruction`/`RefundableInInstruction` payloads supplied by external depositors (the same data path scanned by `extract_serai_data` in `processor/src/networks/bitcoin.rs:493-524`), so an unprivileged depositor can force this branch to execute.

### Impact Explanation
Every Serai Bitcoin transaction carrying a data payload (refund/forward instructions) pays a lower fee rate than requested — by up to ~`fee_per_vbyte * 92` sats — and the fee-shortfall is silently deducted from the multisig's change output. In the worst case, an 80-byte payload pushed onto a transaction already near the standard weight limit produces a signed transaction exceeding `MAX_STANDARD_TX_WEIGHT` that full nodes refuse to relay, stalling movement of multisig funds. Funds are not directly stolen, but the signed transaction does not match the fee/size parameters it was constructed and approved under.

### Likelihood Explanation
Any external Bitcoin user who deposits with attached instruction data (up to the 80-byte limit enforced at `send.rs:171`) deterministically triggers the undercharging branch whenever the processor spends outputs carrying data. No cryptographic assumption or collusion is needed; the miscounting happens on every `data: Some(_)` construction.

### Recommendation
Pass the fully constructed `tx_outs` (or at least the `OP_RETURN` script) into `calculate_weight_vbytes` rather than `payments`, so `weight`, `vbytes`, `needed_fee`, the change delta, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the exact transaction that will be signed. A regression test asserting `tx.weight()` of the produced `SignableTransaction` equals the internal `weight` would catch this class of conditional-field miscounting.

### Proof of Concept
```rust
// Construct a SignableTransaction with a data payload
let st = SignableTransaction::new(
  vec![output],
  &[(payment_script, 1000)],
  Some(change_script),
  Some(vec![0u8; 80]), // allowed by the <= 80 check
  fee_per_vbyte,
).unwrap();

// The actual transaction is larger than what the fee was computed for:
let actual_vbytes = st.transaction().vsize() as u64;
// needed_fee < fee_per_vbyte * actual_vbytes
assert!(st.needed_fee() < fee_per_vbyte * actual_vbytes);
// The ~92 vbytes of the OP_RETURN output are unaccounted, so the effective
// fee rate is below fee_per_vbyte and change is over-credited.
```

Notably, `networks/bitcoin/tests/wallet.rs:269` asserts `needed_fee == tx.vsize() * FEE` — but only for a transaction constructed *without* `data`, so the discrepancy is never exercised in tests.