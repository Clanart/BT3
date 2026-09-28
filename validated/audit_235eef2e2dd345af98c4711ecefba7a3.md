### Title
Scanner reports dust-value outputs as received funds that cannot be spent without losing money - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The external report describes a missing zero-value guard: a user can commit funds to a stream whose `rewardTokenAmount` is 0, so the deposit is locked/consumed with no benefit. The analog in Serai is a missing minimum-value guard: `Scanner::scan_transaction` registers any output whose `script_pubkey` matches a registered offset, regardless of `output.value`. Any unprivileged party who knows a Serai deposit address (script_pubkey is a function of the public group key) can send a dust or zero-value output to it, and it will be reported as a `ReceivedOutput` — i.e., funds reported received that are not economically spendable. `scan_transaction` also scans coinbase outputs in `scan_block`, which are immature and unspendable for 100 blocks, compounding the "reported as received but not usable" class.

### Finding Description
`Scanner` builds a `scripts: HashMap<ScriptBuf, Scalar>` map keyed solely by script (`Scanner::new`, `register_offset`, mod.rs:162-196). In `scan_transaction`, the only predicate is `self.scripts.get(&output.script_pubkey)` — there is no check on `output.value` (mod.rs:199-214). A `ReceivedOutput` is then produced and consumed downstream by `SignableTransaction::new` (send.rs:150-256), which treats every `input.output.value.to_sat()` as spendable input value (send.rs:175).

Two concrete defects follow:

1. **Dust/zero-value inputs are net-negative.** Each input adds ~57.5 vbytes of weight to the transaction (send.rs:62-127). An input worth less than the marginal fee it adds strictly reduces the value available for payments/change. If `input_sat` is still ≥ `payment_sat + needed_fee` the transaction is built and signed anyway (send.rs:215-221), silently burning the difference as fee. If it pushes `input_sat` below the requirement, the entire spend fails with `NotEnoughFunds` even though excluding the dust input would have succeeded — the wallet has no way to exclude it because `SignableTransaction::new` consumes all `inputs` passed to it and `multisig` requires the count to match `self.tx.input.len()` (send.rs:273-285).
2. **Zero-value outputs are fully dead weight.** A 0-sat P2TR output is consensus-valid and matches `script_pubkey`, so it is reported as received, yet contributes nothing while adding weight.

This is the same root cause as the report: a resource commitment (`stake`/`scan`+spend) is accepted against a quantity that should have been rejected as effectively zero, causing loss.

### Impact Explanation
Funds reported received that are not spendable (or that actively destroy value when spent). The coordinator/scanner accounting will count dust outputs as received value; attempting to spend them either fails outright or converts the shortfall into additional fee. An attacker can cheaply grief the wallet by sending many dust outputs to the known address, inflating `input` count, transaction weight (`TooLargeTransaction` at send.rs:241), and fee burn, while the scanner reports them as deposits.

### Likelihood Explanation
Medium. Sending a dust output to a Taproot address requires only knowledge of the address/script_pubkey — publicly derivable from the group key — and a standard Bitcoin transaction. No privileged access, collusion, or key material is needed. Exploitation requires the integrator to treat scanner output as received value and feed it to `SignableTransaction`, which is the documented usage path (`ReceivedOutput` → `SignableTransaction::new`).

### Recommendation
Enforce a minimum-value floor in `Scanner::scan_transaction` (e.g., skip `output.value < DUST`, mirroring `send.rs:32,166-169`), or filter `ReceivedOutput`s by value before passing them to `SignableTransaction::new`. Additionally, exclude coinbase outputs in `scan_block` (or annotate `ReceivedOutput` with maturity) so immature outputs are not treated as spendable. At the signing layer, allow `SignableTransaction::new` to drop uneconomic inputs rather than requiring all provided inputs be signed.

### Proof of Concept
1. Derive a Serai group's P2TR `script_pubkey` via `p2tr_script_buf(tweak_keys(keys).group_key())` — deterministic and public.
2. Broadcast a transaction with `TxOut { value: Amount::from_sat(100), script_pubkey }` (or a 0-value output) to that script.
3. `Scanner::scan_transaction` returns a `ReceivedOutput` for it despite `value < DUST` (mod.rs:205-211).
4. Pass it among `inputs` to `SignableTransaction::new` with a normal `fee_per_vbyte`: the input adds ~57.5 vbytes (~`fee_per_vbyte * 57.5` sats of required fee) while contributing only 100 sats, so the change/payment output is reduced, or `NotEnoughFunds` is returned where a spend excluding the dust input would succeed (send.rs:204-235).
5. Repeat with dozens of dust outputs to force `TooLargeTransaction` or exhaust `input_sat` on fees — griefing achieved entirely with public, unprivileged inputs.