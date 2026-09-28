### Title
OP_RETURN `data` output is excluded from fee/weight accounting, so transactions carrying data underpay the target fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` builds the final output list (`tx_outs`) including an optional OP_RETURN `data` output, but computes the transaction weight/vbytes — and therefore `needed_fee` — from the `payments` slice only. Both the initial calculation and the "with change" recalculation omit the data output. A caller who attaches data gets an economically identical spend that is charged less fee than the requested `fee_per_vbyte` requires, analogous to a swap path that skips the documented swap fee.

### Finding Description
In `networks/bitcoin/src/wallet/send.rs`:

1. Lines 188–202: payment outputs are collected into `tx_outs`, and if `data` is present, a `TxOut` with a `ScriptBuf::new_op_return(...)` script (up to ~80 bytes of payload plus overhead, allowed by the `data.len() <= 80` check at line 171) is pushed onto `tx_outs`.
2. Line 204: `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` computes `(weight, vbytes)` from `payments` — not `tx_outs` — so the OP_RETURN output is never represented in the weight.
3. Lines 206–213: `needed_fee = fee_per_vbyte * vbytes` and the `TooLowFee` minimum-relay check are evaluated against this under-measured `vbytes`.
4. Lines 224–235: the change branch recomputes with `calculate_weight_vbytes(tx_ins.len(), payments, Some(&change))`, again using `payments` and again excluding the data output, so `fee_with_change` and the change amount are also wrong when `data` is set.

`calculate_weight_vbytes` (lines 62–127) faithfully expands a full `Transaction` and uses `tx.weight()` / `get_virtual_tx_size`, but it only sees what it is passed — and it is never passed the data output. `fee()` (lines 138–141) confirms the actual fee is `sum(inputs) - sum(outputs)`; since outputs exclude any compensation for the omitted weight, the realized fee rate is strictly below `fee_per_vbyte` whenever `data.is_some()`.

### Impact Explanation
- The transaction carries up to ~90 extra serialized bytes that were never paid for. The realized fee rate is `needed_fee / actual_vsize < fee_per_vbyte`.
- If `fee_per_vbyte` was chosen at/near the minimum relay rate (the code explicitly validates against `DEFAULT_MIN_RELAY_TX_FEE` at line 211), the real transaction can fall below the network's relay/ mempool acceptance threshold, producing a signed transaction that does not propagate — funds become unconfirmable/unspendable in practice until replaced.
- Even when it does relay, the plan's accounting (`needed_fee`, `post_fee_branches` amortization, change amount) is computed against a fee that does not reflect the actual transaction size, mirroring the reported bug where an equivalent path escapes the documented charge.

### Likelihood Explanation
Reachable by any caller of the public `SignableTransaction::new` API that supplies `Some(data)` — no special privileges, malformed encodings, or adversarial peers required. The undercharge scales linearly with `data.len()` and `fee_per_vbyte`. In-tree callers in `processor/src/networks/bitcoin.rs` pass `None` for `data`, so the defect only triggers for consumers using the OP_RETURN feature, which limits real-world frequency.

### Recommendation
Include the data output in weight accounting: build a combined output list (payments + optional OP_RETURN) and pass it to `calculate_weight_vbytes` — e.g., extend its signature to accept the full `tx_outs`/`&[TxOut]` set, or pass a `data: Option<&ScriptBuf>` parameter that appends the OP_RETURN `TxOut` inside `calculate_weight_vbytes` for both the no-change and with-change calls (lines 204 and 226). Add a test asserting `needed_fee == fee_per_vbyte * signed_tx.vsize()` for a transaction with a 80-byte data payload.

### Proof of Concept
```rust
// Conceptual: any inputs with sufficient funds
let payments = [(p2tr_script_buf(key).unwrap(), 10_000)];
let data = Some(vec![0xaa; 80]); // max permitted payload

let tx = SignableTransaction::new(
    inputs, &payments, Some(change_script), data.clone(), fee_per_vbyte,
).unwrap();

// The signed transaction contains one more output (~80-byte OP_RETURN push)
// than was present in the weight calculation at line 204/226.
let actual_vsize = signed_tx.vsize() as u64;
let actual_feerate = tx.fee() / actual_vsize; // paid fee / real size

// actual_feerate < fee_per_vbyte because ~89+ bytes of OP_RETURN output
// were never counted in `vbytes`/`vbytes_with_change`.
assert!(actual_feerate < fee_per_vbyte);
```
Root cause confirmed at `SignableTransaction::new` (`send.rs` lines 194–235): `tx_outs` includes the OP_RETURN output, while both `calculate_weight_vbytes` invocations receive only `payments`.