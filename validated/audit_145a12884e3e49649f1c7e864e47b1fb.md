### Title
SignableTransaction ignores the OP_RETURN data output when calculating transaction weight, undercharging the fee — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The external report describes an input edge case (a hostname with an escaped trailing dot) reaching a code path that mis-sizes an allocation, producing an out-of-bounds write. In Serai's memory-safe Rust code the analogous bug shape is an attacker-influenced input field which is not accounted for when sizing a structure derived from it. `SignableTransaction::new` appends an OP_RETURN output carrying caller-influenced `data` to `tx_outs`, but then computes the transaction weight/vbytes — and therefore `needed_fee` — using only `payments`, excluding the OP_RETURN output entirely. The constructed transaction is larger than the measured vsize, so the fee it actually pays corresponds to a lower feerate than requested and can fall below the minimum relay feerate despite the `TooLowFee` check passing.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`), the OP_RETURN output is pushed onto `tx_outs` at line 195 before the weight calculation: [1](#0-0) 

However, the call at line 204 passes `payments` — the caller's payment list, which does not include the OP_RETURN output — and `None` for change:

```rust
let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);
```

`calculate_weight_vbytes` builds a model transaction whose outputs come only from `payments` plus an optional change output, so the OP_RETURN output's weight (~ (8-byte amount + varint + up to ~83-byte scriptPubKey) × 4 WU, i.e. roughly 40–45 vbytes for the maximum 80 bytes of data) is never counted. `needed_fee = fee_per_vbyte * vbytes` is therefore underestimated, and the minimum-relay-fee guard at line 211 compares `needed_fee` against `DEFAULT_MIN_RELAY_TX_FEE * vbytes / 1000` using the same undercounted `vbytes`, so it cannot catch the shortfall. The funds sufficiency check at line 215 also uses the underestimated `needed_fee`. When change exists, `calculate_weight_vbytes` is called again with `Some(&change)` (line 226), but still with `payments` only, so the change path underestimates too, and the change amount `input_sat - payment_sat - fee_with_change` is inflated by the same missing fee.

The final `weight` stored/used for the `MAX_STANDARD_TX_WEIGHT` check also excludes the OP_RETURN output when no change output is created, so a transaction marginally under the standardness limit could actually exceed it.

### Impact Explanation
`data` originates from deposit instructions embedded by external users in Bitcoin transactions (see `extract_serai_data` in `processor/src/networks/bitcoin.rs` lines 493–524, which copies up to `MAX_DATA_LEN` bytes of attacker-controlled OP_RETURN/witness data into outputs later forwarded into `SignableTransaction::new`). An unprivileged depositor can therefore force construction of transactions whose actual feerate is materially below the requested `fee_per_vbyte`, and below the relay minimum when the requested rate is near it. Such transactions will be rejected or stall in mempools, rendering scanned multisig outputs temporarily unspendable until a new signing round with corrected parameters is completed, and the `NotEnoughFunds` check may accept input sets that cannot actually cover the true required fee.

### Likelihood Explanation
Any deposit carrying instruction data triggers the OP_RETURN branch. The underestimation is deterministic — it depends only on the presence of `data`, not on adversarial precision — so every data-bearing transaction pays a lower effective feerate than intended. Relay failure additionally requires the chosen `fee_per_vbyte` to be close enough to the minimum that the ~10–45 missing vbytes push the real rate below 1 sat/vB, which narrows the strongest impact case but not the systematic underpayment.

### Recommendation
Pass the already-built `tx_outs` (or an explicit representation of the OP_RETURN output) into `calculate_weight_vbytes` instead of `payments`, in both the no-change call at line 204 and the change call at line 226, so the measured vbytes, `needed_fee`, the relay-minimum guard, the funds check, and the `MAX_STANDARD_TX_WEIGHT` check all reflect the transaction actually being signed.

### Proof of Concept
```rust
// Conceptual PoC against SignableTransaction::new
let payments = [(p2tr_script_buf(key).unwrap(), 10_000u64)];
let data = Some(vec![0xAA; 80]); // maximal attacker-controlled deposit data
let tx = SignableTransaction::new(inputs, &payments, None, data, fee_per_vbyte).unwrap();

// The actual signed transaction contains an extra ~92-byte OP_RETURN output
// that was never included in the vbytes used to compute needed_fee.
let actual_vsize = tx.transaction().vsize() as u64;
assert!(actual_vsize > tx.needed_fee() / fee_per_vbyte);
// Effective feerate = tx.fee() / actual_vsize < fee_per_vbyte;
// for fee_per_vbyte near DEFAULT_MIN_RELAY_TX_FEE this drops below relay minimum.
```

### Citations

**File:** networks/bitcoin/src/wallet/send.rs (L194-206)
```rust
    if let Some(data) = data {
      tx_outs.push(TxOut {
        value: Amount::ZERO,
        script_pubkey: ScriptBuf::new_op_return(
          PushBytesBuf::try_from(data)
            .expect("data didn't fit into PushBytes depsite being checked"),
        ),
      })
    }

    let (mut weight, vbytes) = Self::calculate_weight_vbytes(tx_ins.len(), payments, None);

    let mut needed_fee = fee_per_vbyte * vbytes;
```
