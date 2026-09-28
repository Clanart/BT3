### Title
`Scanner` reports economically unspendable dust outputs as received funds, with no minimum-value check - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Yearn bug class is a hardcoded, inflexible threshold (`maxLoss = 1bps` baked into a fixed selector) that silently converts a legitimate operation into a failure/stuck-funds scenario once conditions exceed the hardcoded bound. In Serai's bitcoin crate, `Scanner::scan_transaction` credits *any* output paying to a registered script, regardless of value, while `SignableTransaction::new` hardcodes `DUST = 546` and refuses to construct transactions paying/spending outputs below economic viability. The two hardcoded policies do not agree, so outputs can be reported as received yet be impossible (or cost-prohibitive) to spend.

### Finding Description
`Scanner::scan_transaction` (networks/bitcoin/src/wallet/mod.rs:199-214) pushes a `ReceivedOutput` for every `tx.output` whose `script_pubkey` is in `self.scripts`, with no check on `output.value`. `scan_block` (lines 221-227) does the same for every transaction including the coinbase (the code itself notes a post-processing pass is required, but no value filter exists anywhere).

`SignableTransaction::new` (networks/bitcoin/src/wallet/send.rs:150-243) enforces the hardcoded `pub const DUST: u64 = 546` (send.rs:32): payments below it error (`DustPayment`), change below it is silently burned into the fee (send.rs:228-234), and `NotEnoughFunds` aborts the spend when `input_sat < payment_sat + needed_fee` (send.rs:215). For a fee rate of `f` sat/vbyte, spending a single Taproot input costs roughly `57 * f` sats plus output/fees; at `f = 10`, an input below ~600-1500 sats is unspendable even though it exceeds `DUST` and was reported by `Scanner`. The threshold hardcoded in the spend path (`546`) is neither aligned with the scan path (no minimum) nor with the fee-dependent real cost of spending.

### Impact Explanation
An unprivileged party sends a Bitcoin transaction paying a tiny amount (e.g., 1-545 sats, or any amount below `fee * vbytes`) to a Serai-controlled script. `Scanner` returns a `ReceivedOutput` for it, so the deposit is reported as received. Any attempt to spend it via `SignableTransaction::new` either triggers `NotEnoughFunds` (fee exceeds input value) or drags fee cost above the credited value. Funds are credited that can never be economically (or at all) spent, matching the "funds reported received that are not spendable" acceptance criterion, and mirroring the Yearn pattern where a fixed parameter (maxLoss / DUST and absent scanner minimum) turns a withdrawal path into a guaranteed failure.

### Likelihood Explanation
Fully reachable by any external party: creating a dust output to a Serai address requires only a normal Bitcoin transaction, which falls under "Bitcoin transactions they send". No collusion, no malicious validator, no leaked keys needed. The only mitigation would be a caller-side value filter, but nothing in the in-scope crate performs it, and the crate's own spend logic proves the mismatch.

### Recommendation
Add a minimum-value check to `Scanner::scan_transaction`/`scan_block` (e.g., skip outputs with `value < DUST`, or a fee-scaled economic minimum), or document and enforce at the scan layer the same bound the spend layer uses, so only spendable outputs are ever reported as `ReceivedOutput`. Ideally compute the spendability bound from `fee_per_vbyte` rather than a single constant.

### Proof of Concept
```rust
// networks/bitcoin context
let mut scanner = Scanner::new(key).unwrap();
// Attacker crafts a tx paying 1 sat to the Serai P2TR script
let tx = Transaction {
    version: Version(2),
    lock_time: LockTime::ZERO,
    input: vec![/* any spendable input */],
    output: vec![TxOut { value: Amount::from_sat(1), script_pubkey: p2tr_script_buf(key).unwrap() }],
};
let outputs = scanner.scan_transaction(&tx);
assert_eq!(outputs.len(), 1);            // reported as received
let received = outputs[0];
// Attempt to spend it: fee alone exceeds the input value
let err = SignableTransaction::new(
    vec![received],
    &[(p2tr_script_buf(key).unwrap(), 546)],
    None, None,
    1, // 1 sat/vbyte
);
assert!(matches!(err, Err(TransactionError::NotEnoughFunds { .. })));
// The credited output can never be spent at any fee rate.
```