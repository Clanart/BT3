### Title
Untrusted `ReceivedOutput` bytes are accepted without binding the claimed `outpoint`/`TxOut` to the offset's `script_pubkey`, letting an attacker fabricate unspendable "received" funds - (File: `networks/bitcoin/src/wallet/mod.rs`)

### Summary
The external report concerns a balance that is minted to the user but never added to `tokensIn`, so real value is not accounted for. The Serai analog lives in the Bitcoin wallet: `ReceivedOutput::read` deserializes an arbitrary `(offset, TxOut, OutPoint)` triple from untrusted bytes, and neither `Scanner`-independent consumers nor `SignableTransaction::multisig` ever verify that the claimed `outpoint` exists on-chain or that the claimed `TxOut.value` matches the real UTXO. The only consistency check performed is `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey` in `send.rs:277`, which binds the *offset* to the *script* but leaves `value` and `outpoint` completely unauthenticated.

### Finding Description
- `ReceivedOutput::read` (`mod.rs:122-134`) accepts `read_F` bytes for `offset` and consensus-decodes `output` and `outpoint` with no cross-validation — an attacker can supply any value/outpoint for a script the key controls, or a duplicate outpoint.
- `SignableTransaction::new` (`send.rs:175`) sums `input.output.value` from these unverified `TxOut`s to determine spendable balance and fee capacity.
- `SignableTransaction::multisig` (`send.rs:273-285`) only checks that each offset's derived key produces the claimed `script_pubkey`. It does not check the `outpoint` resolves to a UTXO paying that script, nor that the claimed value equals the on-chain value.
- `TransactionSignMachine::sign` (`send.rs:373-391`) commits to `Prevouts::All(&self.tx.prevouts)` — the attacker-controlled claimed values — under `TapSighashType::Default`. If the claimed value/outpoint doesn't match reality, the produced signature commits to false prevouts and the resulting transaction is consensus-invalid; the "received" funds were never real or are burned as unspendable.

This is the direct analog of the Aura bug: the accounting layer (`ReceivedOutput` → `input_sat`) records a balance the system cannot actually claim, because a component of the real credit (here: the authentic UTXO binding) is missing from what gets accounted.

### Impact Explanation
An unprivileged party feeding crafted bytes to `ReceivedOutput::read` can cause the multisig to believe it received funds that are not spendable: inflated `value` fields pass the `NotEnoughFunds` check and produce a transaction that either (a) is invalid on-chain (signatures commit to wrong prevout amounts — wasted fee/change UTXOs still get consumed from *real* inputs if mixed), or (b) double-counts a single real output via duplicated `outpoint`s, causing the system to disburse more than it holds. Real, previously scanned outputs mixed into the same `inputs` vector would be genuinely spent, so fabricated entries can drain real funds into fees/payments the balance sheet never had.

### Likelihood Explanation
Reachable wherever `ReceivedOutput`s are deserialized from data an unprivileged party can influence (the type exposes a public `read` from arbitrary `Read`ers). Exploitation requires the crafted output's `script_pubkey` to correspond to a registered offset (trivially satisfied for the base script with `offset = 0`), and the fabricated `outpoint`/`value` are entirely attacker-chosen. Medium likelihood: impact is bounded by whether the consumer also cross-checks against chain data, which the in-scope code does not perform.

### Recommendation
Either remove `ReceivedOutput::read`'s trust assumptions or add validation: (1) in `multisig`/construction, verify each `outpoint`'s confirmed `TxOut` via RPC rather than trusting the embedded `output`; (2) reject duplicate `outpoint`s in `SignableTransaction::new`; (3) at minimum, check `p2tr_script_buf(key + offset·G) == output.script_pubkey` inside `ReceivedOutput::read` or a `verify` method so a forged triple is rejected before reaching accounting.

### Proof of Concept
```rust
// Given Scanner::new(group_key) registered the base script:
let script = p2tr_script_buf(group_key).unwrap();
// Attacker crafts bytes: offset=0, TxOut{value: 100_000_000, script_pubkey: script},
// outpoint pointing at a real 546-sat UTXO (or a nonexistent txid)
let fake = ReceivedOutput::read(&mut crafted_bytes).unwrap();
// SignableTransaction::new sees input_sat = 100_000_000, passes NotEnoughFunds
let stx = SignableTransaction::new(vec![fake], &payments, change, None, fee_rate).unwrap();
// multisig() passes: offset 0 → script matches
// sign() commits Prevouts::All with the fabricated 1 BTC prevout
// Result: consensus-invalid tx, or real co-included inputs spent against phantom balance
```

Note: I could not verify within the allowed iterations whether `processor/src/networks/bitcoin.rs` constructs `ReceivedOutput`s exclusively via `scan_transaction` (which would keep `outpoint`/`value` honest) or also via `ReceivedOutput::read` on peer-supplied data; the vulnerability stands in the in-scope crate's public API surface, but its exploitation path depends on that consumer.