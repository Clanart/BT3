### Title
`ReceivedOutput::read` accepts untrusted offset/output/outpoint tuples with no consistency check, letting an attacker inject phantom "spendable" outputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput` is the wallet's unit of a detected, spendable Bitcoin output: an `offset` scalar (used to re-key the threshold key for that specific input), the `TxOut`, and its `OutPoint`. `Scanner::scan_transaction` constructs these consistently by construction — it looks up the `script_pubkey` in `self.scripts` and attaches the matching registered offset (`networks/bitcoin/src/wallet/mod.rs:205-211`). `ReceivedOutput::read`, however, deserializes the three fields completely independently (`networks/bitcoin/src/wallet/mod.rs:122-134`): it canonically parses the scalar via `Secp256k1::read_F` and consensus-decodes the `TxOut`/`OutPoint`, but never verifies that the claimed `output.script_pubkey` actually equals `p2tr(group_key + generator * offset)`. Any byte string which parses produces a struct the wallet treats as its own spendable output.

### Finding Description
The bug class of CVE-2019-2816 — unauthenticated input accepted by a networking-facing parsing path without semantic validation, enabling unauthorized insertion of attacker-controlled data — maps onto this deserialization path. The in-scope rules explicitly list `ReceivedOutput::read` as a sink for untrusted bytes. The only validation performed is canonicality of the scalar and syntactic validity of the `TxOut`/`OutPoint`; the critical invariant tying the three fields together (that the output pays a P2TR key derived from the wallet's group key tweaked by `offset`) is checked only on the honest `Scanner` construction path, never on the `read` path.

Downstream, `TransactionSignMachine` signs `Prevouts::All(&self.tx.prevouts)` sighashes with per-input re-keyed signature machines (`networks/bitcoin/src/wallet/send.rs:373-397`), trusting the `offset`/`output` pairing to yield a spendable input. A crafted `ReceivedOutput` therefore propagates into transaction construction as a fully trusted coin.

### Impact Explanation
An attacker able to feed serialized `ReceivedOutput` bytes to a wallet instance (e.g., a malformed/malicious output list supplied to a cosigner or reconstructed from an untrusted store) can inject arbitrary "received" outputs: outputs the wallet reports as owned and spendable but which do not pay a key it controls, or which reference nonexistent/wrong-valued prevouts. Consequences include balance inflation (funds reported received that are not spendable) and the wallet building funding transactions over phantom inputs, producing signatures over sighashes committing to attacker-chosen prevout data. This is an integrity violation of the wallet's accessible data reachable purely by malformed input bytes, analogous to the CVE's "unauthorized update, insert or delete access to accessible data".

### Likelihood Explanation
Exploitation requires the attacker to reach a `ReceivedOutput::read` call with chosen bytes — plausible wherever serialized outputs cross a trust boundary (network-supplied output lists, restored state, inter-process messages). It does not require key material, threshold collusion, or malformed curve points; the parse always succeeds. Impact is bounded to integrity/availability of the wallet's coin view (the produced signatures remain valid Schnorr signatures and cannot be redirected to attacker keys, since the tweaked key is still `group_key + offset·G`), consistent with a Medium severity.

### Recommendation
After parsing in `ReceivedOutput::read` (or at `SignableTransaction` construction), recompute the expected script: derive `p2tr_script_buf(key + generator * offset)` and compare against `output.script_pubkey`, rejecting mismatches. Alternatively, re-validate each prevout against `Scanner::scripts` before signing.

### Proof of Concept
```rust
// Given a wallet with group key K, an attacker crafts a ReceivedOutput where
// `offset` is arbitrary and `output.script_pubkey` pays an unrelated P2TR key.
let mut bytes = vec![];
bytes.extend(Scalar::ONE.to_bytes());                       // offset (canonical, parses)
bytes.extend(serialize(&attacker_chosen_txout));            // any syntactically valid TxOut
bytes.extend(serialize(&some_outpoint));                    // any OutPoint

// ReceivedOutput::read succeeds — no consistency check between offset and script_pubkey
let output = ReceivedOutput::read(&mut bytes.as_slice()).unwrap();

// The wallet now treats `output` as spendable. When used in a SignableTransaction,
// TransactionSignMachine signs Prevouts::All with a key re-keyed by `offset`,
// producing a transaction spending an outpoint the wallet cannot actually spend,
// or reporting phantom received funds.
```