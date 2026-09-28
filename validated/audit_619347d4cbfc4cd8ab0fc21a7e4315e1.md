### Title
`SignableTransaction::new` omits the OP_RETURN data output from the weight/vbytes calculation, undercharging fees - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The bug class from the report — a sizing/ratio check whose formula omits a term that will actually be present — has a direct analog in `bitcoin-serai`. `SignableTransaction::new` pushes an OP_RETURN output into `tx_outs` when `data` is specified, but both calls to `calculate_weight_vbytes` pass only `payments` as the output list. The fee and minimum-relay checks are therefore computed over a transaction that is smaller than the one actually built and signed.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150`), the data output is appended to the real output list:

- `tx_outs.push(TxOut { value: Amount::ZERO, script_pubkey: ScriptBuf::new_op_return(...) })` at `send.rs:194-202`.

However, weight/vbytes are computed as:

- `let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);` at `send.rs:204`
- `Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` at `send.rs:225-226`

`calculate_weight_vbytes` (`send.rs:62-127`) reconstructs a template `Transaction` whose `output` vector is built solely from `payments` (plus optional change at `send.rs:95-99`). The OP_RETURN output — up to 80 bytes of data plus script overhead — is never included. Consequently:

- `needed_fee = fee_per_vbyte * vbytes` (`send.rs:206`, `send.rs:227`, `send.rs:232`) undercharges; the effective fee rate of the signed transaction is below the caller-specified `fee_per_vbyte`.
- The minimum-relay check `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` (`send.rs:211`) is evaluated against the underestimated `vbytes`, so a transaction can pass this check while its true fee rate falls below the default minimum relay fee.
- The `MAX_STANDARD_TX_WEIGHT` check (`send.rs:241`) uses `weight` that also excludes the data output, so a transaction exceeding the standardness weight limit can be produced.

This is exactly the reported pattern: a check computed on a total that fails to include an amount (`_loanTokenBalance` missing `+ amount` in the report; the output set missing the OP_RETURN output here), producing a verdict that does not match the real transaction.

### Impact Explanation
An unprivileged caller can supply arbitrary `data` (up to the 80-byte limit enforced at `send.rs:171`) to `SignableTransaction::new`. The resulting signed transaction pays a lower absolute fee than requested and can have a true fee rate below `DEFAULT_MIN_RELAY_TX_FEE`, causing relay/mining failure — funds committed as inputs become stuck in an unbroadcastable/unconfirmable transaction despite all sanity checks passing. It can also emit a non-standard transaction exceeding `MAX_STANDARD_TX_WEIGHT` while reporting success.

### Likelihood Explanation
Any use of the `data` parameter triggers the miscalculation deterministically; the larger the data payload, the larger the divergence between estimated and actual vsize/weight.

### Recommendation
Include the data output in the weight calculation. Build the OP_RETURN `TxOut` before computing weight and pass the full output list (payments + OP_RETURN [+ change]) to `calculate_weight_vbytes`, or add a `data: Option<&ScriptBuf>` parameter to it so the template transaction matches the final one.

### Proof of Concept
```rust
// networks/bitcoin/src/wallet/send.rs
// In SignableTransaction::new:
tx_outs.push(TxOut {                       // send.rs:194-202 — OP_RETURN is a real output
  value: Amount::ZERO,
  script_pubkey: ScriptBuf::new_op_return(data /* up to 80 bytes */),
});

let (mut weight, vbytes) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, None); // send.rs:204 — no data output
// needed_fee = fee_per_vbyte * vbytes                        // send.rs:206 — undercharged
// final tx.output = tx_outs (includes OP_RETURN)             // send.rs:245-251
```
A transaction constructed with `data = vec![0; 80]`, `change = Some(..)`, and `fee_per_vbyte` set exactly to the minimum relay rate passes `TooLowFee`, yet the signed transaction's actual vsize is ~90+ bytes larger, making its real fee rate sub-minimal and non-relayable.