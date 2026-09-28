### Title
`SignableTransaction::new` omits the OP_RETURN `data` output from the weight/vbytes used for fee calculation - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
The ksmbd bug class is a size/length computation that forgets one component of the buffer (the `+1` null terminator), so validation/accounting undercounts the true length. The same shape exists in bitcoin-serai: `SignableTransaction::new` appends the OP_RETURN `data` output to the actual transaction's output list, but then computes the transaction weight, vbytes, `needed_fee`, the dust-limited change amount, and the `MAX_STANDARD_TX_WEIGHT` check using `calculate_weight_vbytes(tx_ins.len(), payments, ...)`, which builds its template transaction purely from `payments` and `change` and never includes the data output.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` at lines 194–202. Only afterwards are fees computed:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
let mut needed_fee = fee_per_vbyte * vbytes;
```

`calculate_weight_vbytes` (lines 62–127) constructs a template `Transaction` whose outputs are only `payments` and optionally `change`; there is no parameter for the data output, so the OP_RETURN output (up to 80 bytes of data plus `script_pubkey` overhead, amount field, and varint) contributes zero to `vbytes` and `weight`. The change-output path at lines 224–235 repeats the same omission when computing `fee_with_change`. Consequences:

- `needed_fee` and the change amount are computed against a vbyte count that is too small, so the constructed transaction pays an effective fee rate strictly lower than the requested `fee_per_vbyte` (by roughly `fee_per_vbyte * (data.len() + ~11 vbytes)`).
- The `TooLowFee` check (line 211) compares against the undercounted `vbytes`, so a transaction can be accepted whose real fee rate falls below `DEFAULT_MIN_RELAY_TX_FEE` once the data output is included.
- The `TooLargeTransaction` check (line 241) uses the same undercounted `weight`, so a transaction that actually exceeds `MAX_STANDARD_TX_WEIGHT` once the data output is added will not be rejected.

Any caller supplying `data` triggers this; no attacker needs key material, only the ability to cause a transaction carrying an OP_RETURN payload to be built.

### Impact Explanation
The transaction produced is still consensus-valid and spends correctly, but it systematically pays less than the intended fee rate whenever `data` is present. In the worst case the real fee rate drops below the relay minimum (the `TooLowFee` check passes against the understated size), producing a transaction that never propagates/confirms — funds that were committed to a fee/change split the caller verified via `needed_fee()`/`fee()` are locked in an unbroadcastable state, and change is over-credited by the missing output's fee cost. This is an incorrect size-accounting bug reachable purely through the public `data` argument.

### Likelihood Explanation
Any use of `SignableTransaction::new` with a non-`None` `data` argument hits the undercount deterministically — it is not probabilistic. Whether it causes a stuck transaction depends on the margin between `fee_per_vbyte` and the relay minimum; for fee rates near minimum or large data payloads the transaction will be rejected by the network. Severity is Medium: no key recovery or forgery, but funds accounting and broadcastability are silently wrong on a public-input-controlled path.

### Recommendation
Pass the serialized OP_RETURN `ScriptBuf` (or the fully-built `tx_outs` minus change) into `calculate_weight_vbytes` so the data output is included in the weight/vbytes used for `needed_fee`, the `TooLowFee` check, the change calculation, and the `TooLargeTransaction` check. Concretely, extend `calculate_weight_vbytes` to accept the data output's script and push `TxOut { value: Amount::ZERO, script_pubkey: op_return_script }` into the template transaction, mirroring how the OP_RETURN output is pushed at lines 194–202.

### Proof of Concept
```rust
// Construct any spendable output `output` and keys as in tests/wallet.rs.
let inputs = vec![output];
let data = vec![0u8; 80]; // maximum allowed OP_RETURN payload

let tx = SignableTransaction::new(
  inputs, &[], None, Some(data), FEE,
).unwrap();

// The reported needed fee was computed without the OP_RETURN output.
// Actual vsize exceeds the vbytes used for needed_fee by the size of
// the data output (~ 8 + varint + 1 + 1 + 80 bytes of base weight).
let actual_vbytes = u64::try_from(tx.transaction().vsize()).unwrap();
let implied_vbytes = tx.needed_fee() / FEE;
assert!(actual_vbytes > implied_vbytes); // fee rate < requested FEE

// With FEE chosen at the relay minimum, the real tx falls under
// DEFAULT_MIN_RELAY_TX_FEE and is unbroadcastable despite passing
// the TooLowFee check.
```