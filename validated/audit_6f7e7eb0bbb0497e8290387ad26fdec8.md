### Title
`ReceivedOutput::read` credits an attacker-declared `TxOut` value without verifying the output's script or existence, reporting unspendable funds as received - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput::read` deserializes an offset, a `TxOut`, and an `OutPoint` from untrusted bytes and trusts all three fields verbatim. It never checks that `output.script_pubkey` is the P2TR script derived from the scanner key plus `offset`, nor that the claimed `value` corresponds to any real on-chain output. This mirrors the fee-on-transfer bug class: a credited/recorded amount (here `ReceivedOutput::value()`) is taken from an attacker-supplied declared value rather than the amount actually present and spendable.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`:

- `ReceivedOutput::read` (lines 122–134) reads `offset` via `Secp256k1::read_F`, then consensus-decodes a `TxOut` (which includes `value` and `script_pubkey`) and an `OutPoint`, returning `ReceivedOutput { offset, output, outpoint }` with no consistency check.
- `ReceivedOutput::value()` (line 117) returns `self.output.value.to_sat()` — purely the declared field.
- `Scanner::scan_transaction` (lines 199–214) produces legitimately-derived `ReceivedOutput`s by matching `output.script_pubkey` against the registered `scripts` map, but `read` bypasses this: a deserialized `ReceivedOutput` can claim any `script_pubkey` and any `value` under any `offset`.
- Downstream, `SignableTransaction::new` (send.rs line 175) sums `input.output.value` as `input_sat` to decide sufficiency of funds, and `multisig` (send.rs lines 273–285) only rejects at signing time when `p2tr_script_buf(offset.group_key())` doesn't match `prevouts[i].script_pubkey` — meaning a forged `ReceivedOutput` with a correctly-matching script but inflated `value` and a fabricated `outpoint` passes all checks and reports a balance that does not exist on-chain.

### Impact Explanation
An unprivileged party that can feed bytes to `ReceivedOutput::read` (an explicitly-accepted untrusted sink in scope) can cause the consumer to treat phantom or inflated funds as received: the reported `value` exceeds the actual spendable amount, exactly like a vault crediting `amount` while only `amount - fee` arrived. The wallet then constructs transactions believing it holds `input_sat` sats when the real UTXO set contains less or nothing at the claimed `outpoint`. This is the enumerated impact "funds reported received that are not spendable"; every spend plan built on the forged output is unexecutable, and accounting based on `value()` is corrupted by arbitrary attacker-chosen amounts.

### Likelihood Explanation
Reachable wherever serialized `ReceivedOutput`s cross a trust boundary (e.g., outputs reported by another component or peer and rehydrated via `read`). The attacker only controls public inputs — the bytes fed to `read` — and needs no key material. Exploitation requires the consumer to trust the deserialized output's claimed value; the script-vs-offset check in `multisig` can be bypassed by honestly matching the script (which is derivable: `p2tr_script_buf(key + G*offset)`) while lying about `value` and `outpoint`.

### Recommendation
`ReceivedOutput` should not carry a free-standing `TxOut`/`OutPoint` pair from `read`. Either:
- Store and read only the `OutPoint`, resolving the `TxOut` from chain data at use time, or
- After `read`, verify `output.script_pubkey == p2tr_script_buf(key + GENERATOR * offset)` for the expected scanner key, and verify the claimed `outpoint`/`value` against the actual UTXO before crediting it — i.e., compute received value from chain state rather than declared bytes, analogous to measuring balance-before/balance-after instead of trusting the permit `amount`.

### Proof of Concept
```rust
// Attacker constructs bytes for ReceivedOutput::read
let mut buf = Vec::new();
// offset chosen so key + G*offset yields an even P2TR key
buf.extend(real_offset.to_bytes());
// TxOut with the CORRECT script_pubkey but an inflated value
let forged = TxOut {
  value: Amount::from_sat(1_000_000_000), // no such funds exist
  script_pubkey: p2tr_script_buf(scanner_key + ProjectivePoint::GENERATOR * real_offset).unwrap(),
};
buf.extend(serialize(&forged));
// An outpoint that does not exist, or exists with a lower value
buf.extend(serialize(&OutPoint::new(fake_txid, 0)));

let ro = ReceivedOutput::read(&mut &buf[..]).unwrap();
assert_eq!(ro.value(), 1_000_000_000); // credited, never verified

// SignableTransaction::new now treats input_sat as >= 1_000_000_000
// and multisig() succeeds since the script matches the offset —
// producing a transaction spending a nonexistent/misvalued UTXO.
```