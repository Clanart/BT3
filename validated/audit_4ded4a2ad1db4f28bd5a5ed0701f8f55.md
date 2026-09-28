### Title
Scanner::scan_block reports immature coinbase outputs as spendable ReceivedOutputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Mattermost Zoom advisory (CWE-863) is a missing-validation bug: a state-changing endpoint accepted requests without checking that the caller was authorized for the affected resource, letting any logged-in user alter another channel's restrictions. The analog in Serai is `Scanner::scan_block`, which scans every transaction in a block — including the coinbase — and returns `ReceivedOutput` values that are indistinguishable from ordinary, immediately-spendable outputs. The miner who authors the coinbase transaction is an unprivileged party supplying a public input (a Bitcoin transaction in a block), and the resulting output violates Bitcoin's 100-block maturity restriction: the funds are reported received but are not spendable.

### Finding Description
`Scanner::scan_transaction` iterates `tx.output` and matches `output.script_pubkey` against the registered script map, returning a `ReceivedOutput { offset, output, outpoint }` for each match (networks/bitcoin/src/wallet/mod.rs:199-214). `scan_block` then applies this to `block.txdata` wholesale, explicitly including `block.txdata[0]`:

```rust
for tx in &block.txdata {
  res.extend(self.scan_transaction(tx));
}
```

(networks/bitcoin/src/wallet/mod.rs:221-227). The doc comment acknowledges the hazard — "This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed" — but no enforcement exists, and `ReceivedOutput` carries no flag letting a caller distinguish a coinbase output from a normal one after the fact (the struct only stores `offset`, `output`, and `outpoint`, lines 89-97). A caller cannot reliably perform the suggested post-processing: `is_coinbase()` is a property of the spending transaction, not of the `TxOut`/`OutPoint` retained, and by the time the output is spent the only artifact is the outpoint.

Notably, the downstream consumer in `processor/src/networks/bitcoin.rs:691` does skip the coinbase (`for tx in &block.txdata[1 ..]`), demonstrating the requirement is real — but the in-scope `Scanner::scan_block` API itself omits the check, and any caller using it (the natural API for "scan a block") silently receives immature outputs mixed with spendable ones.

### Impact Explanation
A miner (or pool) can craft a coinbase transaction paying to a script_pubkey registered with the Scanner — e.g., the threshold group's tweaked P2TR key or any registered offset script. Any system feeding `scan_block` results into balance accounting or a spend queue will record funds as received that cannot be moved for 100 blocks. If the immature output is selected as a spend input before maturity, the constructed transaction is invalid and the signing round produces a transaction the network rejects; worse, the coinbase output's outpoint can become permanently unspendable if the block is later orphaned, leaving credited "funds" that never existed. This is precisely the accepted impact class "funds reported received that are not spendable."

### Likelihood Explanation
Reachability is straightforward: the only requirement is that a block's coinbase pays to one of the scanner's registered scripts. Miners are unprivileged parties and control coinbase outputs entirely. The probability depends on whether any in-scope caller uses `scan_block` on full blocks rather than `scan_transaction` over `txdata[1..]`; the API presents `scan_block` as the block-scanning entry point and buries the maturity caveat in a doc comment, so misuse is plausible rather than requiring an adversarial integrator. Severity is medium: no key or nonce leakage, but incorrect reporting of received funds with downstream signing/accounting consequences.

### Recommendation
Skip `block.txdata[0]` inside `scan_block` (mirroring `processor/src/networks/bitcoin.rs:691`), or — if coinbase visibility is genuinely desired — add an `is_coinbase: bool` (or maturity height) field to `ReceivedOutput` so callers can enforce the 100-block rule. The former is the minimal fix matching the documented intent.

### Proof of Concept
```rust
// A miner builds a coinbase paying to the scanner's key script.
let mut scanner = Scanner::new(key).unwrap(); // key: even-y ProjectivePoint
let script = p2tr_script_buf(key).unwrap();

let coinbase = Transaction {
  version: Version::TWO,
  lock_time: LockTime::ZERO,
  input: vec![TxIn {
    previous_output: OutPoint::null(), // coinbase input
    ..Default::default()
  }],
  output: vec![TxOut { value: Amount::from_sat(50_0000_0000), script_pubkey: script }],
};

let block = Block { header: .., txdata: vec![coinbase] };

// scan_block returns a ReceivedOutput for the coinbase despite the
// 100-block maturity rule. There is no field on ReceivedOutput marking
// it immature, and it is indistinguishable from a spendable output.
let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1);
// outputs[0] is reported as received yet cannot be spent for 100 blocks
// and is permanently unspendable if the block is orphaned.
```
The in-scope `scan_transaction`/`scan_block` code at networks/bitcoin/src/wallet/mod.rs:199-227 contains no coinbase/maturity handling; the only mitigation is a doc comment requiring an impossible-after-the-fact post-processing pass, since `ReceivedOutput` (lines 89-97) retains nothing that identifies the output as coinbase-derived.