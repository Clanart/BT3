### Title
Coinbase outputs reported as immediately spendable `ReceivedOutput`s — deposits credited in the window before maturity, and scheduled spends produce invalid transactions - ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
The bug class of the external report is a *two-phase accounting window*: value exists inside the system in a state where it is not yet realized at its true worth, and an actor who inserts an action between the "loss/not-yet-realized" phase and the "profit/realized" phase extracts or misallocates value. In `bitcoin-serai`, `Scanner::scan_block` iterates **every** transaction in a block — including `txdata[0]`, the coinbase — and returns matched outputs as `ReceivedOutput` structs that are indistinguishable from spendable ones. A coinbase output is consensus-immature for 100 blocks. `ReceivedOutput` carries only `offset`, `output`, and `outpoint` (no maturity flag), and `SignableTransaction::new` / `TransactionMachine` perform no coinbase or maturity check before producing a signed spend. The result is the same shape as the report: funds are *recognized* in one phase (scanning/crediting) while in a second, later phase they are revealed as not actually spendable — and actions taken in between (scheduling a batch spend, crediting a deposit) are mispriced.

### Finding Description
`Scanner::scan_block` calls `scan_transaction` on all of `block.txdata` (`mod.rs:221-227`), and `scan_transaction` pushes a `ReceivedOutput` for any output whose `script_pubkey` matches a registered script (`mod.rs:199-214`). Nothing checks `Transaction::is_coinbase()`. The only safeguard is a doc comment stating "a post-processing pass is needed" — yet `ReceivedOutput`/`Output` do not expose whether the origin transaction was a coinbase, so downstream code consuming `ReceivedOutput`s (serialization round-trips through `ReceivedOutput::read` at `mod.rs:122-134`, `Output` in `processor/src/networks/bitcoin.rs`) cannot reliably distinguish them after the fact without re-fetching the block.

`SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) and `SignableTransaction::multisig` (`send.rs:273-285`) validate fee/dust/change math and that `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`, but never check coinbase maturity. A `TransactionMachine` will therefore drive the full FROST ceremony and emit a validly-signed transaction that Bitcoin consensus rejects as immature, or — if used as credited deposit before maturity — reports balance that cannot be moved.

The confirmation pipeline scans blocks and emits `ScannerEvent::Block { outputs }` which are saved via `ScannerDb::save_outputs` (`processor/src/multisigs/scanner.rs:562-567, 699-704`); coinbase outputs ride this same path when the scan includes `txdata[0]` (the in-tree test itself relies on this: `networks/bitcoin/tests/wallet.rs:63-70` asserts a coinbase output is returned by `scan_block`).

### Impact Explanation
An unprivileged miner (any party able to produce a block is an untrusted input source to the scanner; the scan consumes raw block data) pays the multisig's registered `script_pubkey` in a coinbase. The processor reports the output as received funds. Two concrete harms, mirroring the report's "temporary mispriced state":

1. **Funds reported received that are not spendable**: the deposit is emitted/saved as a normal output yet cannot be spent for 100 blocks. If credited (e.g., a mint against it), value is issued against funds the multisig cannot yet move — the analog of the report's temporarily-depressed share valuation.
2. **Poisoned batch**: once the immature output enters the scheduler's input set, `SignableTransaction` builds and the threshold signs a transaction spending an immature coinbase. The signed transaction is unbroadcastable; worse, a *reorg of the coinbase* between scan and broadcast invalidates the entire batch, and every legitimate payment batched with it is voided — a griefing/DoS vector funded only by a block the miner was going to mine anyway.

### Likelihood Explanation
Requires an attacker to mine a block paying to Serai's script — economically meaningful on mainnet but no privilege is required, and the external precondition parallels the report's "there must be collateral gains in the stability pool." Whether the production `get_outputs` path filters `txdata[0]` determines exploitability; the wallet crate itself unconditionally returns coinbase outputs and provides no field on `ReceivedOutput` to detect them post hoc, so any consumer that scans whole blocks (the natural use of `scan_block`) is exposed. Severity is bounded by the documented comment, but the API makes the documented mitigation awkward (the information needed is dropped from `ReceivedOutput`).

### Recommendation
- In `Scanner::scan_block`, skip `block.txdata[0]` or filter coinbase transactions (`tx.is_coinbase()`), matching the comment's own suggested alternative.
- Alternatively/additionally, store an `is_coinbase` (or `maturity_height`) flag on `ReceivedOutput` so `ReceivedOutput::read`/`write` preserve it and `SignableTransaction::new` can reject immature inputs.
- In `processor/src/networks/bitcoin.rs::get_outputs`, ensure the coinbase transaction is excluded before outputs are emitted via `ScannerEvent::Block`.

### Proof of Concept
```rust
// networks/bitcoin — demonstrates scan_block returns an immature,
// unspendable coinbase output indistinguishable from a normal deposit.
// (Mirroring the existing test at networks/bitcoin/tests/wallet.rs:63-70)

let block = rpc.get_block(&rpc.get_block_hash(coinbase_block_number).await.unwrap()).await.unwrap();
assert!(block.txdata[0].is_coinbase());

let outputs = scanner.scan_block(&block);
assert_eq!(outputs.len(), 1);
let immature = outputs[0].clone(); // ReceivedOutput with no maturity marker

// A consumer cannot tell this output is immature:
let rt = ReceivedOutput::read::<&[u8]>(&mut immature.serialize().as_ref()).unwrap();
assert_eq!(rt, immature); // round-trips; still no coinbase flag

// SignableTransaction happily builds a spend of it, and the multisig signs —
// producing a transaction consensus-invalid for ~100 blocks:
let tx = SignableTransaction::new(
  vec![immature],
  &[(p2tr_script_buf(key).unwrap(), 10_000)],
  Some(change_script),
  None,
  FEE,
).unwrap();
let signed = sign(&keys, &tx); // valid signature over an unbroadcastable tx
// rpc.send_raw_transaction(&signed) -> rejected: "bad-txns-premature-spend-of-coinbase"
```

Caveat: whether the processor's `get_outputs` path forwards `txdata[0]` was not fully verified within the available iterations; the wallet-layer defect (`scan_block`/`scan_transaction` returning untagged immature outputs) is confirmed by `mod.rs:221-227` and the test at `networks/bitcoin/tests/wallet.rs:63-70`. If production scanning already excludes the coinbase, severity reduces accordingly.