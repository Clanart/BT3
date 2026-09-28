### Title
OP_RETURN data output excluded from transaction weight lets a signer underpay the required fee - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` appends an OP_RETURN output carrying caller-supplied `data` to `tx_outs`, but computes the transaction weight/vbytes — and therefore `needed_fee` — from `payments` only, omitting the data output entirely. A caller can attach up to 80 bytes of data while paying a fee and passing the minimum-relay-fee check as if that output did not exist, the direct analog of acquiring something (an extra output / data embedding) without paying its required cost.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`, the OP_RETURN output is pushed onto `tx_outs` at lines 194-202:

```rust
if let Some(data) = data {
  tx_outs.push(TxOut {
    value: Amount::ZERO,
    script_pubkey: ScriptBuf::new_op_return(...),
  })
}
```

However, the weight and vbyte calculation at line 204 uses `payments`, not the final output list:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` (lines 62-127) builds a transaction whose `output` vector is derived solely from `payments` plus an optional change output — the OP_RETURN output is never included. Consequently:

1. `needed_fee = fee_per_vbyte * vbytes` (line 206) is understated by the vsize of the data output (~8 bytes value + script overhead + up to 80 bytes of data, roughly 90-95 vbytes for max-size data).
2. The minimum relay fee check `needed_fee < (DEFAULT_MIN_RELAY_TX_FEE * vbytes) / 1000` (line 211) is evaluated against the understated vbytes, so it can pass even when `needed_fee` is below the minimum relay fee for the transaction's real size.
3. The `MAX_STANDARD_TX_WEIGHT` check at line 241 uses the understated `weight`, so a transaction that actually exceeds the standard weight limit can be accepted.
4. The same omission occurs in the change branch (line 225-226): `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))` also ignores the data output, so `fee_with_change` and the change amount `input_sat - (payment_sat + fee_with_change)` are computed as if the OP_RETURN output were absent.

The resulting `SignableTransaction` commits to `tx` containing the data output (line 245-251), and `fee()` (lines 138-141) will report `inputs - outputs` correctly, but `needed_fee` and the relay/weight policy checks are all computed against a smaller phantom transaction. This is reachable by any party able to cause a `SignableTransaction` to be constructed with `data` — the cost of embedding up to 80 bytes on-chain is simply not charged.

### Impact Explanation
- Fee evasion: the transaction pays `fee_per_vbyte * vbytes_without_data` instead of the fee for its actual size — an unprivileged caller obtains an on-chain output/data embedding without paying for its weight, matching the bug class of minting without paying the required fee.
- Stuck/unrelayable funds: if the understated `needed_fee` falls below the real minimum relay fee for the actual vsize, the signed transaction will be rejected by relay policy. The spent `ReceivedOutput` inputs are consumed by the plan, but the transaction cannot confirm — funds are effectively locked until a replacement is manually constructed.
- Standardness bypass: a transaction whose true weight exceeds `MAX_STANDARD_TX_WEIGHT` by up to ~360 WU (90 bytes of uncounted output) can be produced, again yielding a non-relayable transaction.

### Likelihood Explanation
Triggering requires only calling `SignableTransaction::new` with `data = Some(_)` and a `fee_per_vbyte` near the minimum relay threshold, or with enough inputs/outputs that the extra ~90 uncounted bytes push the true size past policy limits. The miscalculation is unconditional whenever `data` is specified — the fee is always underpaid by the data output's vsize.

### Recommendation
Include the OP_RETURN output in the weight calculation. Either extend `calculate_weight_vbytes` to take the full `tx_outs` list (payments + data + optional change), or append the data output to the `payments`-derived output list inside it before computing `tx.weight()`. Re-derive `vbytes`, `needed_fee`, the minimum-fee check, and the `MAX_STANDARD_TX_WEIGHT` check from the complete output set.

### Proof of Concept
```rust
// Conceptual; assumes an available ReceivedOutput `input` of sufficient value.
let inputs = vec![input];
let payments = vec![(p2tr_script_buf(key).unwrap(), 10_000)];
let data = vec![0u8; 80]; // max allowed

let fee_per_vbyte = 5; // >= min relay for small TX
let tx = SignableTransaction::new(inputs, &payments, None, Some(data), fee_per_vbyte)
  .unwrap();

// needed_fee was computed on a TX *without* the OP_RETURN output.
let declared = tx.needed_fee();
// Real size of the signed transaction includes the 80-byte pushdata output:
let real_vsize = tx.transaction().vsize() as u64; // + ~91 vbytes unaccounted
let required = fee_per_vbyte * real_vsize;

assert!(declared < required); // fee underpaid by ~455 sats
// If inputs - payments == declared exactly, the actual feerate is
// declared / real_vsize < fee_per_vbyte, and may fall below min relay.
```