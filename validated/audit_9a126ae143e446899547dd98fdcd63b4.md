### Title
`ReceivedOutput::read` trusts deserialized `offset`/`TxOut`/`OutPoint` bytes without binding them, letting attacker-crafted data be reported as spendable received funds - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The external report's bug class is an external call whose "success" result is trusted while the returned payload (`_resultData`) is never validated against expectations, letting malicious data be interpreted as benign. The in-scope analog lives in `ReceivedOutput::read` (`networks/bitcoin/src/wallet/mod.rs:122-134`): it reads an `offset` scalar, a `TxOut`, and an `OutPoint` from raw bytes and returns a fully-formed `ReceivedOutput` with no check that the claimed fields are self-consistent or correspond to any real, spendable UTXO. The deserialization succeeding is treated as proof the output was legitimately received, exactly like `require(_success)` being treated as proof the returned data is well-formed.

### Finding Description
`ReceivedOutput` couples three independent claims: the scalar `offset` (the key tweak needed to spend), the `output` (`TxOut` carrying `value` and `script_pubkey`), and the `outpoint` (the claimed funding location). `read` (lines 122-134) only performs structural decoding — `Secp256k1::read_F` for the offset, `consensus_decode` for the `TxOut` and `OutPoint` — then returns `Ok(ReceivedOutput { offset, output, outpoint })`. There is no verification that:

- `output.script_pubkey` equals `p2tr_script_buf(key + offset * G)` for the multisig key (the offset↔script binding), so an arbitrary output can be paired with an arbitrary offset;
- `output.value` corresponds to the actual on-chain amount at `outpoint`;
- `outpoint` references an existing output at all.

`SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) then consumes these untrusted fields directly: `offsets` are taken from `input.offset` (line 176), `input_sat` is summed from `input.output.value` (line 175), and `prevouts` are drained from the untrusted `TxOut`s (line 253). The only semantic check downstream is in `SignableTransaction::multisig` (lines 273-285), which verifies `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` — it binds the offset to the script but **not** the value or the outpoint. Because the sighash uses `Prevouts::All` (line 375), the attacker-chosen values are committed into the message the threshold signs.

### Impact Explanation
An unprivileged party supplying crafted bytes to `ReceivedOutput::read` produces a `ReceivedOutput` that claims funds were received (a chosen `value` at a chosen `outpoint` with a self-consistent `script_pubkey`/`offset` pair that passes the `multisig` check) but does not correspond to a spendable UTXO. The result is "funds reported received that are not spendable": the protocol's balance/accounting treats attacker-declared value as real inputs, and a threshold signing round is spent producing a BIP-340-valid signature on a transaction Bitcoin consensus will reject (nonexistent outpoint or mismatched prevout amount). Beyond the wasted signing session, fabricated high-value inputs can push `SignableTransaction::new` past `NotEnoughFunds` checks and cause real spendable inputs to be co-mingled into a dead transaction.

### Likelihood Explanation
Reachable whenever serialized `ReceivedOutput` bytes cross a trust boundary — the sibling `Output::read` in the processor reads `ReceivedOutput` from stored/passed data, and the type's public `read`/`serialize` API exists precisely so untrusted persistence/network bytes become `ReceivedOutput`s. The attacker needs no key material: `read` succeeds on any canonically-encoded scalar plus well-formed `TxOut`/`OutPoint`. Mitigating factors: the script_pubkey must still be a real scanner-registered P2TR script for `multisig` to accept it, and the forged signature cannot steal funds — the impact is fabricated reported balances and invalid signed transactions rather than theft. This places it at Medium severity.

### Recommendation
In `ReceivedOutput::read` (or a dedicated validated constructor), verify semantic consistency in addition to structural decode: check `output.script_pubkey == p2tr_script_buf(key + G * offset)` for the expected key — mirroring the check already performed in `SignableTransaction::multisig` — and have callers confirm `outpoint`/`value` against the chain before the output is reported as received. Analogous to the report's advice: where specific return data is expected, verify it before acting on it rather than treating successful decoding as sufficient.

### Proof of Concept
1. Compute a registered scanner script `S = p2tr_script_buf(key + G * offset)` for a chosen `offset` (attacker knows the multisig's public key and can pick `offset` freely, or reuse a legitimately registered one).
2. Craft serialized bytes: `offset || TxOut { value: <inflated amount>, script_pubkey: S } || OutPoint { txid: <nonexistent or unrelated>, vout: 0 }`.
3. `ReceivedOutput::read` returns `Ok`, reporting `value()` as the inflated amount with a valid spend key relationship.
4. `SignableTransaction::new(vec![forged], payments, change, None, fee)` accepts the input and `multisig(&keys)` returns `Some(...)` because the script↔offset check passes; `sign`/`complete` produce a transaction whose prevout commitment (via `Prevouts::All` at `send.rs:375`) references the fabricated outpoint/value — a signed transaction consensus will reject, while the fabricated balance was already accounted as received.