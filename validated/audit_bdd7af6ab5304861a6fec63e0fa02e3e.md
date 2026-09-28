### Title
Scanner accepts dust/zero-value outputs with no minimum-amount check, letting anyone force Serai to hold unspendable "received" inputs that cost more in fees than their value - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The external report describes a griefing primitive: an unprivileged caller invokes `bid` with `_componentAmount = 0`, paying almost nothing while forcing the protocol to run `accrueProtocolFee` and mint inflationary fees against SetToken holders. The analog in Serai's in-scope code is the Bitcoin wallet's `Scanner`/`SignableTransaction` pair: any transaction output paying to a scanned `script_pubkey` is registered as a `ReceivedOutput` with no minimum-value check, and `SignableTransaction::new` enforces `DUST` only on *payments* and *change*, never on *inputs*. An attacker can therefore send dust — including a 0-satoshi output, which is consensus-valid — to Serai's Taproot address and have it reported as received funds that are not economically spendable, since spending a Taproot input costs ~57.5 vbytes of fee, more than a dust input's value.

### Finding Description
`Scanner::scan_transaction` in `networks/bitcoin/src/wallet/mod.rs` (lines 199-214) iterates `tx.output` and pushes a `ReceivedOutput` for every output whose `script_pubkey` matches a registered script. It checks only the script — there is no `output.value` floor:

```rust
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput { offset: *offset, output: output.clone(), ... });
}
```

`ReceivedOutput` is then consumed by `SignableTransaction::new` in `networks/bitcoin/src/wallet/send.rs` (lines 150-256). That constructor validates `inputs.is_empty()`, rejects dust *payments* (`*amount < DUST` → `DustPayment`, line 166), and only creates a change output when the leftover `value >= DUST` (line 229). But it never checks `input.output.value.to_sat()` against `DUST` or against the marginal fee cost of carrying that input. Each input unconditionally contributes to `input_sat`, `offsets`, `tx_ins`, `prevouts`, and the per-input `AlgorithmMachine`/`SighashCache` signing work in `TransactionSignMachine::sign` (lines 373-397).

### Impact Explanation
- **Funds reported received that are not spendable**: `scan_transaction`/`scan_block` return these outputs as received balance, yet a 0-sat or dust input can never be spent profitably — including it strictly reduces the value available for payments/fees (the input adds ~57.5 vbytes but contributes ≤546 sats, below any sane `fee_per_vbyte` break-even). Unlike the Index report where griefing minted inflationary fees, here the attacker forces the multisig to either (a) permanently track dead UTXOs, or (b) burn fees by aggregating worthless inputs.
- **Fee griefing at scale**: Bitcoin consensus permits `TxOut.value = 0` (only overflow/`>MAX_MONEY` is rejected) and permits many outputs per tx, so a single attacker transaction can create hundreds of matched dust/zero outputs. `SignableTransaction::new` will still attempt to build and sign transactions over them; each added input inflates `needed_fee` while adding nothing to `input_sat`.
- **Forced signing work**: `TransactionMachine::preprocess` and `TransactionSignMachine::sign` create one full FROST `AlgorithmMachine` + BIP-341 sighash per input, so dust inputs also amplify the threshold signing round cost.

### Likelihood Explanation
Reachable by any unprivileged party who can broadcast a Bitcoin transaction paying to Serai's publicly known Taproot `script_pubkey` — no validator status, no collusion, no malformed encoding needed. The cost is bounded only by the dust value (as low as 0 sats) plus the attacker's own tx fee, directly paralleling the "bid with 0 amount" cheapness of the source bug. `scan_block` even scans the coinbase, compounding exposure.

### Recommendation
Enforce a minimum value on scanned/spent inputs, mirroring the existing `DUST` payment check:

- In `Scanner::scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:199-214`), skip outputs with `output.value.to_sat() < DUST` (or a configurable economic floor covering the marginal input fee at the target fee rate).
- In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs`), reject or filter inputs whose value is below the marginal cost of including them (`fee_per_vbyte * per_input_vbytes`), returning a new `TransactionError` variant so callers can drop uneconomic UTXOs rather than burn fees on them.

### Proof of Concept
```rust
// Attacker broadcasts a tx with an output paying 0 sats to Serai's script.
let dust_output = TxOut {
  value: Amount::ZERO, // or Amount::from_sat(1..=546)
  script_pubkey: serai_p2tr_script.clone(), // matches scanner.scripts
};

// networks/bitcoin/src/wallet/mod.rs: scan_transaction
let received = scanner.scan_transaction(&attacker_tx);
assert_eq!(received.len(), 1);        // reported as received
assert_eq!(received[0].value(), 0);   // worth nothing

// networks/bitcoin/src/wallet/send.rs: SignableTransaction::new
// The 0-value input is accepted unconditionally — no DustPayment-style check
// exists for inputs. It joins input_sat/tx_ins/prevouts and triggers a
// per-input FROST AlgorithmMachine + taproot_key_spend_signature_hash in
// TransactionSignMachine::sign, while adding ~57.5 vbytes of needed_fee and
// zero spendable value. The output is reported received but is never
// economically spendable, and forcing the multisig to sweep it burns fees.
```

Severity assessment: Medium — no key/share recovery or forged signatures, but a concrete, cheap, unprivileged path to "funds reported received that are not spendable" plus forced fee/signing-cost griefing, matching the bug class of the source report (missing minimum-amount guard enabling cost-free griefing).