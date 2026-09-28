### Title
`ReceivedOutput::read` accepts any `offset`/`TxOut` pair without checking they correspond, producing outputs reported as received which `SignableTransaction::multisig` can never sign — funds locked with no alternative destination (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
`ReceivedOutput::read` deserializes an `offset` scalar, a `TxOut`, and an `OutPoint` from untrusted bytes, but never verifies the core invariant the `Scanner` upholds at construction time: that `group_key + offset*G` maps (through `p2tr_script_buf`) to `output.script_pubkey`. When such a `ReceivedOutput` is later spent, `SignableTransaction::multisig` re-derives `keys.offset(offset)` and compares `p2tr_script_buf(offset.group_key())` against `prevouts[i].script_pubkey`; on any mismatch it returns `None`, and there is no API to re-target, correct, or abandon the output. This is the same shape as the reference issue: a value is claimable only through one rigidly-fixed derivation path, and if that path is invalid the funds are permanently locked rather than redirectable.

### Finding Description
- `Scanner::scan_transaction` only creates `ReceivedOutput`s whose `offset` was looked up in `self.scripts`, so scanned outputs always satisfy `p2tr_script_buf(key + offset*G) == output.script_pubkey`. (`networks/bitcoin/src/wallet/mod.rs:199-214`)
- `ReceivedOutput::read` performs no equivalent check: it reads `offset` via `Secp256k1::read_F`, then consensus-decodes an arbitrary `TxOut` and `OutPoint`, and returns the struct unconditionally. (`networks/bitcoin/src/wallet/mod.rs:122-134`)
- `SignableTransaction::new` stores `input.output` verbatim as `prevouts` and `input.offset` as `offsets`. (`networks/bitcoin/src/wallet/send.rs:175-185,245-255`)
- `SignableTransaction::multisig` rejects the entire transaction if any single input's derived script doesn't match the claimed prevout script, returning `None`. (`networks/bitcoin/src/wallet/send.rs:273-285`)
- Downstream, `Output::key()` reconstructs the owning group key as `xonly(script) - offset*G` (processor/src/networks/bitcoin.rs:112-122); a mismatched offset yields a group key that no threshold key set controls, so `split_outputs_by_key`/`Plan` construction attributes or asserts on the wrong key, and signing either panics or fails for every attempt.

### Impact Explanation
An output recorded with an inconsistent `offset` is reported as received funds (`ReceivedOutput::value`, `balance()`) yet is cryptographically unspendable by the intended key set: `multisig` returns `None` for it deterministically, forever. Since the Bitcoin wallet API offers no recovery/redirection path — every spend must go through the fixed `offset`/prevout pair embedded in the `SignableTransaction` — the claimed value is locked exactly as in the reference finding's blacklisted-recipient scenario. Medium severity: loss requires a corrupted/hostile `ReceivedOutput` record reaching the spend path, but once present there is no remediation.

### Likelihood Explanation
`scan_transaction`-produced outputs are safe; the exposure is any path where `ReceivedOutput::read` (or the processor `Output::read` wrapping it, which also decodes attacker-influenced serialized fields) is fed bytes not produced by a `Scanner`. Within the stated threat model, `ReceivedOutput::read` is an accepted untrusted-bytes sink, so an unprivileged party able to supply or corrupt a serialized output (peer-provided data, crafted `offset` chosen so `key()` collides with a different multisig key) can manufacture this state. Not remotely triggerable on an honest scanner alone, hence Medium not High.

### Recommendation
Re-establish the scanner's invariant at deserialization time. Either:
- Have `ReceivedOutput::read` take the scanner `key` and verify `p2tr_script_buf(key + GENERATOR*offset) == Some(output.script_pubkey)`, or
- Add a `ReceivedOutput::valid_for(key) -> bool` check invoked in `SignableTransaction::new`/`multisig` and in processor `Output` ingestion, so malformed records are rejected before an output is recorded as received, rather than failing irrecoverably at signing.

### Proof of Concept
```rust
// Key the multisig controls
let key = even_key(); // group key with even Y

// Real on-chain output paying to the multisig script
let real_txout = TxOut {
  value: Amount::from_sat(100_000),
  script_pubkey: p2tr_script_buf(key).unwrap(),
};
let outpoint = OutPoint::new(txid_of_funding_tx, 0);

// Attacker/corruption supplies a mismatched offset (never registered for `key`)
let bad_offset = Scalar::random(rng);

let mut bytes = Vec::new();
bytes.extend(bad_offset.to_bytes());
bytes.extend(serialize(&real_txout));
bytes.extend(serialize(&outpoint));

// Deserialization succeeds — no consistency check
let received = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// The output is reported as received funds
assert_eq!(received.value(), 100_000);

// But it can never be spent: derived script != prevout script -> multisig() -> None
let tx = SignableTransaction::new(vec![received], &payments, change, None, 1).unwrap();
assert!(tx.multisig(&threshold_keys).is_none()); // funds permanently locked
```