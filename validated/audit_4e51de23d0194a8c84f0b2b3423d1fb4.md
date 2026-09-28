### Title
`SignableTransaction::new` calculates the transaction weight and fee before accounting for the OP_RETURN `data` output, so data-carrying transactions silently underpay the requested fee rate - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
Analogous to the Astaria `claim()` bug — which measured the contract's balance *after* distribution instead of before — `SignableTransaction::new` measures the transaction's weight using only the `payments` list, i.e., a snapshot of the outputs that omits the OP_RETURN output already appended for `data`. The resulting `needed_fee` (and the change-output calculation derived from it) is computed against a transaction state that does not reflect the final transaction, so the actual fee rate paid is lower than `fee_per_vbyte` by the entire serialized size of the data output.

### Finding Description
In `SignableTransaction::new`, the OP_RETURN output is pushed onto `tx_outs` first (networks/bitcoin/src/wallet/send.rs:194-202), but the weight/vbyte measurement is then performed with `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` (line 204) — passing `payments`, which does not contain the `data` output. The same `payments` argument is reused when recomputing weight with change (lines 225-227).

`calculate_weight_vbytes` builds a mock `Transaction` whose `output` list is derived only from `payments` plus an optional change output (lines 85-99). Consequently, `weight`/`vbytes` never include the OP_RETURN output's ~91 vbytes for a maximum-size (80-byte) payload (8-byte value + varint + `OP_RETURN` opcode + push opcode + 80 bytes of data).

Concretely:
- `needed_fee = fee_per_vbyte * vbytes` (line 206) under-counts the required fee by `fee_per_vbyte * ~91` (proportional to data length).
- The `TooLowFee` check (lines 211-213) validates this understated fee, so a data transaction whose *actual* fee rate is below the minimum relay fee still passes.
- The `NotEnoughFunds` check (line 215) and the change computation `input_sat.checked_sub(payment_sat + fee_with_change)` (line 228) both use the understated fee, so change is over-credited by the same amount — the uncounted bytes are paid for by... nobody; the effective sat/vbyte of the broadcast transaction is just lower than requested.

### Impact Explanation
Any caller creating a `SignableTransaction` with `data` (OP_RETURN payload up to 80 bytes, line 171) produces a transaction paying less than the requested fee rate. With a large payload and a fee rate near `DEFAULT_MIN_RELAY_TX_FEE`, the real fee rate can fall below the minimum relay threshold even though `TooLowFee` was checked — the transaction is rejected by node policy, leaving the transfer unconfirmed (funds stuck until a correctly-priced transaction is re-signed). At higher rates the transaction still confirms but at a materially lower priority than requested, and `needed_fee()`/`fee()` callers are misinformed about the true rate achieved.

### Likelihood Explanation
Deterministic whenever `data` is `Some`; the miscalculation scales linearly with payload length (up to ~91 vbytes at the 80-byte cap). Whether it prevents confirmation depends on how close `fee_per_vbyte` is to the minimum relay floor, but the miscalculation itself occurs on every data-carrying transaction.

### Recommendation
Pass the complete output set to `calculate_weight_vbytes` — i.e., build the mock transaction from `tx_outs` (or extend `payments` with the OP_RETURN `TxOut`) before computing weight, so `needed_fee`, the `TooLowFee`/`NotEnoughFunds` checks, and the change calculation all reflect the transaction's final size.

### Proof of Concept
```rust
// inputs: a single ReceivedOutput worth 100_000 sats
// payments: [(addr, 50_000)], change: Some(addr), data: Some(vec![0; 80])
let tx = SignableTransaction::new(inputs, &payments, change, Some(vec![0; 80]), 1000).unwrap();
// needed_fee was priced for a tx WITHOUT the OP_RETURN output
assert_eq!(tx.needed_fee(), 1000 * vbytes_without_data);
// The real transaction contains the extra ~91-byte OP_RETURN output;
// actual rate = fee() / real_vsize < 1000 sats/vbyte requested.
```
In `calculate_weight_vbytes` (`send.rs:62-127`), the mock `tx.output` is built solely from `payments` (lines 85-93) plus optional change (lines 95-99); the OP_RETURN output added to `tx_outs` at lines 194-202 is never represented, so `tx.weight()` and the derived vbytes/fees exclude it.