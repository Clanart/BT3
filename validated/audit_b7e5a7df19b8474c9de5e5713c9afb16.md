### Title
Fee and weight calculation omits the OP_RETURN output, underpaying the requested feerate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an OP_RETURN output carrying caller-supplied `data` to `tx_outs`, but both calls to `calculate_weight_vbytes` are made with the `payments` slice, which never includes the data output. The transaction that gets sized and priced is therefore *not* the transaction that gets signed and broadcast — analogous to the referenced issue where the oracle returns a price denominated in 3CRV while the protocol treats it as USD. The measured object differs from the real object, and the difference is silently absorbed.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` at lines 194–202:

```rust
// networks/bitcoin/src/wallet/send.rs
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}
```

However, the weight/vbytes computation at line 204 and the with-change recomputation at lines 225–226 both call:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
...
let (weight_with_change, vbytes_with_change) =
  Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
```

`payments` is the original `&[(ScriptBuf, u64)]` parameter, which does not contain the OP_RETURN output. `calculate_weight_vbytes` (lines 62–127) builds a template `Transaction` only from `payments` and `change`, so the serialized length contributed by the OP_RETURN output — 8 bytes of value, 1 byte of script length, and `1 + push_opcode + data.len()` script bytes (up to ~92 serialized bytes, ~23 weight units... i.e. ~23 additional vbytes-equivalent weight, roughly 22–23 vbytes) — is never counted.

Consequences:
- `needed_fee = fee_per_vbyte * vbytes` (line 206) and `fee_with_change` (line 227) are both computed on the underestimated size. The actual signed transaction is larger than the priced transaction, so its real feerate is `fee_per_vbyte * vbytes_est / vbytes_real`, strictly below the caller-requested rate.
- The `TooLowFee` check (line 211) compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes_est / 1000`. Both sides use the underestimated `vbytes`, so a transaction can pass this check while its *actual* feerate is below Bitcoin Core's minimum relay feerate, making it non-standard/unrelayable on the real network.
- The `MAX_STANDARD_TX_WEIGHT` check (line 241) uses `weight` that excludes the data output, so a transaction genuinely over the standardness weight limit can be constructed without `TooLargeTransaction` being raised.

Since `TransactionSignMachine::sign` hashes the *real* transaction (including the OP_RETURN output) via `SighashCache` (lines 373–390), the signatures are valid — the defect is purely that the economic sizing used the wrong reference transaction.

### Impact Explanation
Any caller passing `data` receives a `SignableTransaction` whose `needed_fee()` and internal `weight` are smaller than reality. The signed transaction pays a lower effective feerate than requested; at the boundary (fee rate near the relay minimum), the fully signed transaction cannot be broadcast through standard mempool policy, leaving the inputs unspendable through this path until the ceremony is rerun with a higher `fee_per_vbyte`. The `MAX_STANDARD_TX_WEIGHT` bound can likewise be silently exceeded. No funds are stolen and no signatures are forged, so the severity is Medium rather than High.

### Likelihood Explanation
Deterministic whenever `data` is provided: the OP_RETURN output is unconditionally added to `tx_outs` but is never present in the `payments` argument used for sizing. It requires no adversarial timing and is reachable by any unprivileged caller of the public `SignableTransaction::new` API.

### Recommendation
Include the OP_RETURN output in the transaction template used by `calculate_weight_vbytes`. Either pass `tx_outs`-equivalent data (after appending the data output) into `calculate_weight_vbytes` for both the no-change and with-change computations, or restructure the function to take `&[TxOut]` so the same output list is used for sizing, fee computation, and signing.

### Proof of Concept
```rust
// In a test context with a funded ReceivedOutput `output`:
let data = vec![0u8; 80];
let tx = SignableTransaction::new(
  vec![output],
  &[(p2tr_script_buf(key).unwrap(), 1000)],
  None,
  Some(data),
  20, // sat/vbyte
).unwrap();

// needed_fee was computed for a tx WITHOUT the OP_RETURN output
let estimated_vbytes = tx.needed_fee() / 20;
// The real transaction carries the data output
let real_vbytes = tx.transaction().vsize() as u64;
// real_vbytes > estimated_vbytes: actual feerate = needed_fee / real_vbytes < 20
assert!(real_vbytes > estimated_vbytes);
```