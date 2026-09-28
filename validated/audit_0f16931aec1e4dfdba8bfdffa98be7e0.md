### Title
`Scanner::scan_transaction` accepts zero-value outputs, reporting unspendable funds as received - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Derby report's bug class is: an entry-point accepts a `0`-valued request, records it in state, and the recorded value can never be utilized because every downstream consumer requires the value to be non-zero. Serai's Bitcoin wallet exhibits the same shape: `Scanner::scan_transaction` / `Scanner::scan_block` register any output paying to a watched `script_pubkey` as a `ReceivedOutput` without checking `output.value`, so a zero-value (or economically-unspendable dust) output created by any unprivileged third party is reported to the wallet owner as received funds that cannot be spent.

### Finding Description
`Scanner::scan_transaction` matches outputs purely on `script_pubkey` and pushes a `ReceivedOutput` for every match, with no minimum-value check:

```rust
// networks/bitcoin/src/wallet/mod.rs:199-213
pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
  ...
  if let Some(offset) = self.scripts.get(&output.script_pubkey) {
    res.push(ReceivedOutput {
      offset: *offset,
      output: output.clone(),
      outpoint: OutPoint::new(tx.compute_txid(), vout),
    });
  }
```

`ReceivedOutput` carries the `TxOut` verbatim, so `output.value()` may be `0`. The wallet crate itself never validates received amounts: the only value checks in `networks/bitcoin/src/wallet/send.rs` are on *outgoing* payments (`*amount < DUST` → `TransactionError::DustPayment`, send.rs:165-169) and on change (send.rs:228-234). Inputs are summed and used unconditionally (`input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum()`, send.rs:175), so a scanned zero-value output is indistinguishable in bookkeeping from a real payment.

The analog to the Derby issue is precise: `withdrawalRequest(0)` recorded an allowance of `0` that the rest of the protocol treated as a live, non-removable request; here a 0-value output is recorded as a live received output, yet spending it yields nothing — it only adds ~57 vbytes of input weight (per the weight commentary in `processor/src/networks/bitcoin.rs`) that must be paid in fees. The value can never be recovered, but it is indelibly reported as "received."

This is reachable by an unprivileged party: anyone can broadcast a Bitcoin transaction containing a `TxOut { value: 0, script_pubkey: <scanned script> }` (consensus-valid; only non-standard for relay, and minable), or a dust output to the vault/forwarded address, and the wallet will surface it as a `ReceivedOutput`. Note the Serai processor separately filters `output.balance().amount.0 >= N::DUST` (`processor/src/multisigs/scanner.rs:564`), but that is out-of-scope downstream code; the in-scope wallet `Scanner` library — which is a public API usable standalone (`Scanner::new`, `register_offset`, `scan_transaction`, `scan_block`) — performs no such check and documents only the coinbase-maturity caveat (`scan_block` docs, mod.rs:216-220), not the zero/dust-value caveat.

### Impact Explanation
Funds are reported as received that are not spendable: a `ReceivedOutput` with `value == 0` (or below the fee cost of its input weight) is permanently tracked as a deposit. Any accounting, crediting, or automated input-selection built on `scan_transaction`/`scan_block` treats the output as a real receipt of funds. An attacker can gratuitously inflate the reported balance of any scanned key at the cost of mining a dust/zero-value output, and any `SignableTransaction` constructed including such an input burns fee for zero value. Severity: Medium (accounting integrity / unspendable-funds impact, no direct theft).

### Likelihood Explanation
Likelihood is moderate. It requires only a standard transaction submission to Bitcoin — no validator status, no collusion, no leaked material. The trigger condition (a 0-value or dust output to a watched script) is trivially creatable by any sender, including miners. The only mitigation is that downstream Serai processors happen to filter by `N::DUST`, and standard relay policy makes broadcast harder (though not impossible) for zero-value outputs.

### Recommendation
Add a minimum-value check in `Scanner::scan_transaction` (or at `ReceivedOutput` construction) rejecting outputs whose value cannot cover their spend cost — e.g., a `MIN_OUTPUT_VALUE`/`DUST` constant applied to `output.value` before pushing to `res` — mirroring the fix in the Derby report (`require(value > 0)`) and matching the `DUST` semantics already enforced for outgoing payments in `send.rs:165-169`.

```rust
if output.value < Amount::from_sat(DUST) {
  continue;
}
if let Some(offset) = self.scripts.get(&output.script_pubkey) { ... }
```

### Proof of Concept
On a regtest/test network, for `scanner = Scanner::new(key)`:

```rust
let tx = Transaction {
  version: Version(2),
  lock_time: LockTime::ZERO,
  input: vec![/* any funded input */],
  output: vec![
    TxOut {
      value: Amount::from_sat(0), // zero-value output to the watched script
      script_pubkey: p2tr_script_buf(key).unwrap(),
    },
  ],
};

let outputs = scanner.scan_transaction(&tx);
// BUG: outputs.len() == 1 and outputs[0].value() == 0
// The wallet reports a received output that can never be spent for value.
assert_eq!(outputs.len(), 1);
assert_eq!(outputs[0].value(), 0); // recorded as received, unspendable
```

Including this `ReceivedOutput` in `SignableTransaction::new(inputs, payments, ...)` succeeds (send.rs performs no input-value check), contributing 0 sats while adding input weight payable as fee — the direct analog of `withdrawalRequest(0)` recording an unusable zero-value state.