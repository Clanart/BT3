### Title
`SignableTransaction::new` does not validate input uniqueness or offset/script consistency, producing consensus-invalid transactions that the threshold multisig will sign - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The reported bug class — missing or insufficient input validation (uniqueness checks, boundary conditions, consistency between paired values) — maps onto `bitcoin-serai`'s transaction builder. `SignableTransaction::new` accepts a `Vec<ReceivedOutput>` as spendable inputs but performs no validation that (a) the `outpoint`s are unique, or (b) each `ReceivedOutput`'s `offset` actually corresponds to the `script_pubkey` of its `output`. Since `ReceivedOutput::read` deserializes all three fields verbatim from untrusted bytes, an attacker who can feed crafted `ReceivedOutput`s into the builder causes the threshold group to run a full FROST signing ceremony over a transaction Bitcoin consensus will reject.

### Finding Description
`SignableTransaction::new` consumes `inputs` directly into `tx_ins` and `prevouts`:

- `input_sat` is summed over `inputs` (`send.rs:175`), and each input's `offset`/`output`/`outpoint` is trusted as internally consistent (`send.rs:176-185`).
- There is no deduplication of `input.outpoint`. Two `ReceivedOutput`s referencing the same outpoint both become `TxIn`s (analogous to the report's un-validated `_loanNFTs` list).
- There is no check that `output.script_pubkey == p2tr_script_buf(key + GENERATOR * offset)`. `ReceivedOutput::read` (`wallet/mod.rs:122-134`) deserializes `offset`, `output`, and `outpoint` from attacker-controlled bytes with zero cross-field validation.
- Additionally, `input_sat`, `payment_sat + needed_fee` (`send.rs:215`), and `payment_sat + fee_with_change` (`send.rs:228`) are plain `u64` additions/sums over values whose `TxOut` amounts are attacker-controlled via `consensus_decode` — they can wrap (release) or panic (debug) rather than erroring.

A duplicated outpoint makes `input_sat` double-count that output's value, inflating the change/fee computation, and produces a transaction Bitcoin rejects under the `bad-txns-inputs-duplicate` consensus check. A mismatched offset produces a Schnorr signature under the wrong tweaked key, making that input's witness invalid. Either way the multisig completes a signing session for an unbroadcastable transaction.

### Impact Explanation
The threshold signing group is induced to produce signatures for a transaction that can never be included in a block. The signing session, any `CachedPreprocess` material consumed, and coordinator round-trips are wasted, and any fee/output accounting done upstream on the assumption the inputs sum to `input_sat` is wrong (double-counted inputs inflate the apparent fee/change). Inputs are also accepted whose declared value bears no relation to reality (the on-chain output), so fee/`NotEnoughFunds` decisions are computed on attacker-chosen numbers — including arithmetic that can wrap `u64` on crafted `Amount` values.

### Likelihood Explanation
Reachable wherever `ReceivedOutput`s are reconstructed from serialized data or an untrusted source before being passed to `SignableTransaction::new`. `ReceivedOutput::read` explicitly performs no consistency checks, so any pipeline persisting or relaying outputs (rather than using them fresh from `Scanner::scan_transaction`, which does guarantee offset/script consistency) exposes this. Honest-but-buggy callers can also hit it by accidentally supplying overlapping coin selections.

### Recommendation
In `SignableTransaction::new` (or in `ReceivedOutput` validation at read time):

1. Reject duplicate `outpoint`s: `if !seen.insert(input.outpoint) { Err(..) }`.
2. Where the group key is known (e.g., at `multisig()` construction), verify `p2tr_script_buf(key + GENERATOR * input.offset) == Some(input.output.script_pubkey)` so an offset can never be paired with a script it does not unlock.
3. Use `checked_add`/`checked_sub` for `input_sat`, `payment_sat`, and the `payment_sat + needed_fee` / `payment_sat + fee_with_change` sums, returning an error instead of wrapping/panicking.
4. Bounds-check decoded `TxOut` values against `Amount::MAX_MONEY` when reading `ReceivedOutput`.

### Proof of Concept
```rust
// networks/bitcoin (wallet)
// Two ReceivedOutputs referencing the same outpoint are accepted:
let dup = output.clone(); // same outpoint as `output`
let tx = SignableTransaction::new(
    vec![output, dup],          // no uniqueness check -> accepted
    &payments, Some(change), None, fee_per_vbyte,
).unwrap();
// tx.input now contains two TxIn's spending the same OutPoint.
// input_sat double-counts the value, so change/fee are computed on phantom funds.
// After multisig signing, Bitcoin consensus rejects it (bad-txns-inputs-duplicate).

// Similarly, an offset that doesn't match the script:
let forged = ReceivedOutput::read(&mut crafted_bytes).unwrap();
// crafted_bytes: offset = O_a, output.script_pubkey = script for key + O_b*G (a != b)
// The signature is produced under the O_a tweak -> invalid witness for that input.
```

Key code locations: input ingestion and summation at `networks/bitcoin/src/wallet/send.rs:175-185`, funds check at `send.rs:215-221`, change handling at `send.rs:224-235`, and the unchecked deserializer at `networks/bitcoin/src/wallet/mod.rs:120-134`.