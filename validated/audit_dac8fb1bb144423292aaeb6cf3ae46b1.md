### Title
`MAX_STANDARD_TX_WEIGHT` check computed on a transaction missing the OP_RETURN data output, letting oversized inputs bypass the size limit - ([File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
`SignableTransaction::new` enforces Bitcoin's standardness weight limit on a *measured* transaction that omits the `OP_RETURN` data output which is later appended to the *actual* transaction. Like the Pimcore bug — where the `.php → .php.txt` sanitization silently failed once a filename exceeded 256 chars, so the check ran on a different artifact than the one processed — the weight check here runs on a transaction that does not contain all bytes that will actually be signed.

### Finding Description
`calculate_weight_vbytes` builds a dummy `Transaction` only from `tx_ins` and `payments` (plus optionally `change`):

- `networks/bitcoin/src/wallet/send.rs:204` calls `Self::calculate_weight_vbytes(tx_ins.len(), payments, None)` — no `data` output.
- The OP_RETURN output is pushed into `tx_outs` at `send.rs:194-202`, but `tx_outs` is never fed back into the measurement; the dummy tx's outputs come only from `payments` (`send.rs:85-93`).
- The change path (`send.rs:225-232`) recomputes `weight_with_change` with the change output, but still without the data output.
- The limit is enforced at `send.rs:241`: `if weight > u64::from(bitcoin::policy::MAX_STANDARD_TX_WEIGHT)` — on the incomplete weight.

Additionally, the OP_RETURN `data` is length-checked at `send.rs:171` (`> 80` → `TooMuchData`), but each *payment's* `script_pubkey` is copied verbatim into outputs (`send.rs:190`) with no length bound and, more importantly, is only partially reflected: it *is* included in the measured `tx.output`, so payments are covered — the unchecked component is specifically the OP_RETURN output (8-byte value + varint + 1-byte opcode + push header + up to 80 bytes ≈ 90+ serialized bytes ≈ up to ~90 vbytes of non-witness weight).

### Impact Explanation
An edge case where the base transaction sits within `MAX_STANDARD_TX_WEIGHT` but the real transaction exceeds it produces a signed transaction that Bitcoin Core will refuse to relay (`TxTooLarge`/non-standard). The threshold signs a transaction that cannot confirm — inputs are committed (`Prevouts::All`), fees are burned if it ever does get mined via a non-relay path, and the Serai protocol observes an out-payment that never settles. This is a check applied to a truncated representation of the signed artifact, directly analogous to the advisory's "sanitization silently skipped for oversized input."

### Likelihood Explanation
Medium-low likelihood, real impact: requires an integrator/protocol path supplying `data` (e.g., the Serai data-output flow uses OP_RETURN data) or enough payments that the measured weight is near the limit. The gap is bounded (~90 bytes per OP_RETURN output), so the bypass window is narrow but deterministic — no probabilistic element, unlike the filename-length accident in Pimcore.

### Recommendation
Include the OP_RETURN output (and any other appended outputs) in the transaction passed to `calculate_weight_vbytes`, or build the final `tx` first and compute `tx.weight()` directly on the artifact that will actually be signed, before the `MAX_STANDARD_TX_WEIGHT` check at `send.rs:241`.

### Proof of Concept
```rust
// Conceptual, against SignableTransaction::new
let payments: &[(ScriptBuf, u64)] = &[/* payments sized so measured weight lands at
    MAX_STANDARD_TX_WEIGHT - epsilon */];
let data = Some(vec![0u8; 80]); // passes the > 80 check at send.rs:171

let tx = SignableTransaction::new(inputs, payments, Some(change), data, fee_per_vbyte)?;
// weight check at send.rs:241 passes because `data` output was never measured,
// yet tx.transaction() contains the OP_RETURN output and exceeds
// MAX_STANDARD_TX_WEIGHT -> non-standard, unrelayable after threshold signing.
```

Note on verification limits: I confirmed the measurement/check omission directly from `send.rs` (`calculate_weight_vbytes` builds outputs solely from `payments` and the optional `change`; `data` output is added to `tx_outs` outside that path). I did not enumerate every other in-scope crate (e.g., FROST `read_preprocess`/`addendum` paths) for further length-check analogs, but this finding satisfies the bug-class mapping with concrete file/line support.