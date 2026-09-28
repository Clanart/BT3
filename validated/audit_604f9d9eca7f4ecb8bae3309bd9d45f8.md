### Title
`ReceivedOutput::read` accepts an `offset`/`script_pubkey` pair that cannot correspond, admitting outputs that are later unspendable - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`ReceivedOutput` couples a scalar `offset` with a `TxOut`/`OutPoint`, where the offset is the only thing that makes the output spendable: the spendable key is `group_key + G * offset` and `SignableTransaction::multisig` re-derives `p2tr_script_buf(offset.group_key())` and silently returns `None` if it does not equal `prevouts[i].script_pubkey`. `ReceivedOutput::read` deserializes untrusted bytes into this structure with zero consistency checks — it never verifies that the `output.script_pubkey` is even a P2TR output, let alone that it matches the claimed offset — directly analogous to `mintForToken` failing to reject `to == address(0)`.

### Finding Description
- `ReceivedOutput::read` at `networks/bitcoin/src/wallet/mod.rs:122-134` reads `offset` via `Secp256k1::read_F`, then `TxOut` and `OutPoint` via `consensus_decode`. Any canonical scalar and any arbitrary `script_pubkey` (including a non-P2TR or burn script) is accepted.
- The only legitimate producer, `Scanner::scan_transaction` (`mod.rs:199-214`), always emits a consistent pair because it looks up `self.scripts.get(&output.script_pubkey)`; the invariant "script == p2tr(key + G*offset)" is implicit and never enforced at the deserialization boundary.
- Consumers treat a `ReceivedOutput` as spendable value: `SignableTransaction::new` sums `input.output.value` into `input_sat` (`send.rs:175`) for fee/solvency decisions, and `multisig` (`send.rs:273-285`) is where the inconsistency is finally discovered — it returns `None`, meaning the output can never be signed for. There is no error path distinguishing "wrong keys" from "the deserialized offset never matched this script".

### Impact Explanation
An attacker who can feed bytes to `ReceivedOutput::read` (a documented untrusted entry point) can cause funds to be reported as received under an offset that does not spend them — e.g., offset `X` paired with a script_pubkey belonging to `key + G*Y`, or a non-Taproot/op_return-style burn script. The balance is credited (offset, value, outpoint are all attacker-claimed), yet `multisig` permanently returns `None` for the input, rendering the reported funds irretrievable — the same "minted to an unspendable/zero destination" loss as the reference issue. Additionally, a `ReceivedOutput` carrying a bogus `outpoint`/`value` can be used to inflate `input_sat` in `SignableTransaction::new`, biasing fee and change calculation.

### Likelihood Explanation
Requires an attacker to control serialized `ReceivedOutput` bytes presented to a consumer (e.g., a stored/relayed output record). The format itself is attacker-malleable and the validation gap is deterministic — no race or probabilistic element. Severity is Medium because the failure mode is unspendable/misreported funds rather than key or signature compromise.

### Recommendation
Validate the pairing at the trust boundary. Options: (a) make `ReceivedOutput` un-deserializable without its `Scanner` — i.e., a `Scanner::read_output(key, bytes)` that recomputes `p2tr_script_buf(key + G*offset)` and rejects mismatches; (b) at minimum, reject non-P2TR `script_pubkey`s and store the associated key so `read` can verify `script_pubkey == p2tr(key + G*offset)`. Also have `SignableTransaction::new`/`multisig` distinguish "offset does not match script" as an explicit error instead of conflating it with wrong keys.

### Proof of Concept
```rust
// networks/bitcoin: craft a ReceivedOutput whose offset does not match its script
let real_key = /* tweaked group key */;
let mut scanner = Scanner::new(real_key).unwrap();
let offset = scanner.register_offset(Scalar::random(&mut OsRng)).unwrap();
let real_script = p2tr_script_buf(real_key + ProjectivePoint::GENERATOR * offset).unwrap();

// Attacker bytes: correct offset, but a *different* valid P2TR script_pubkey
let forged = ReceivedOutput {
  offset,                                         // claims spendability under key + G*offset
  output: TxOut { value: Amount::from_sat(100_000),
                  script_pubkey: p2tr_script_buf(real_key).unwrap() }, // mismatched script
  outpoint: OutPoint::new(Txid::all_zeros(), 0),
};
let bytes = forged.serialize();
let output = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // accepted, no validation

// Consumer reports 100k sats received, but:
let tx = SignableTransaction::new(vec![output], &payments, None, None, FEE).unwrap();
assert!(tx.multisig(&keys).is_none()); // funds permanently unspendable
```