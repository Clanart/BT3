### Title
A single attacker-supplied unspendable output included among a `SignableTransaction`'s inputs poisons the entire batch spend, making all payments in it fail — (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The reported bug class is: a loop over a list where one invalid element causes the whole claim to revert, so an attacker who can insert a bad entry permanently blocks honest payouts. In `bitcoin-serai`, `SignableTransaction` commits to *all* inputs at once: `sign` uses `Prevouts::All(&self.tx.prevouts)` so every input's signature commits to every other input's prevout, and `multisig`/`complete` produce one transaction containing all inputs. If any single `ReceivedOutput` in `inputs` references an outpoint that doesn't exist, was already spent, or is otherwise unspendable (e.g. an immature coinbase output, which `Scanner::scan_block` explicitly includes), the entire transaction is invalid and every payment bundled with it fails — exactly the refunded-NFT-poisons-claim shape.

### Finding Description
- `Scanner::scan_block` scans `block.txdata` including the coinbase transaction, returning `ReceivedOutput`s that are unspendable for 100 blocks. The doc comment acknowledges this requires an external post-processing filter that the crate itself does not provide (`networks/bitcoin/src/wallet/mod.rs:216-227`).
- `Scanner::scan_transaction` matches purely on `script_pubkey`, so anything paying to a registered script becomes a `ReceivedOutput` with no further spendability validation (`networks/bitcoin/src/wallet/mod.rs:199-214`).
- `ReceivedOutput::read` deserializes `offset`, `TxOut`, and `OutPoint` from untrusted bytes with no consistency or spendability check — a caller can inject an `OutPoint` that does not exist on chain (`networks/bitcoin/src/wallet/mod.rs:122-134`).
- `SignableTransaction::new` copies every supplied `ReceivedOutput` into `tx.input`/`prevouts` without checking that any outpoint is confirmed, unspent, or mature (`networks/bitcoin/src/wallet/send.rs:175-185`, `245-255`).
- `TransactionSignMachine::sign` computes each input's sighash over `Prevouts::All(&self.tx.prevouts)`, so all inputs are bound into a single atomic transaction (`networks/bitcoin/src/wallet/send.rs:373-390`). One bad input invalidates the transaction for every input and every payment in it.
- `SignableTransaction::multisig` additionally aborts the whole machine (`None?`) if any single input's offset doesn't map to its prevout script (`networks/bitcoin/src/wallet/send.rs:275-283`).

### Impact Explanation
An attacker who gets one bad `ReceivedOutput` into a batch (by paying an immature coinbase to the multisig script, or via untrusted bytes consumed through `ReceivedOutput::read`, or by racing a spend of an output the batch assumes exists) makes the entire constructed transaction unbroadcastable — all honest payments/withdrawals bundled in that batch revert, mirroring the claim transaction reverting on a refunded NFT. Since every signature commits to all prevouts, the batch cannot partially succeed; the whole multisig spend fails until the poisoned input is manually removed.

### Likelihood Explanation
Reaching this requires the poisoned output to be selected into `inputs`, which is done by upstream scheduling code outside this crate's scope, and for coinbase outputs requires a miner (or a pool paying the multisig) as the source. The `scan_block` coinbase inclusion is documented, which weakens the finding; the `ReceivedOutput::read` injection path depends on callers feeding untrusted bytes into it. Because there is no in-crate guard and partial failure is impossible, likelihood is moderate but contingent on caller behavior.

### Recommendation
Have `SignableTransaction::new` (or a constructor-level validation step) verify each input's `outpoint` is confirmed, unspent, and mature — e.g., filter or reject coinbase outputs within `scan_block`/`scan_transaction` rather than relying on a documented caller-side pass, and validate `ReceivedOutput`s read via `ReceivedOutput::read` against the chain before batching. Alternatively, structure spending so a single invalid input aborts only that input's inclusion, not the entire transaction.

### Proof of Concept
```rust
// Attacker mines (or a pool pays) the multisig's P2TR script in the coinbase.
// Scanner::scan_block returns it as a ReceivedOutput.
let outputs = scanner.scan_block(&block); // includes immature coinbase output

// The poisoned output is batched with honest outputs.
let mut inputs = outputs;
inputs.extend(honest_outputs);

let stx = SignableTransaction::new(inputs, &payments, Some(change), None, fee).unwrap();
let machine = stx.multisig(&keys).unwrap();
// signing succeeds, but the final Transaction is consensus-invalid for 100 blocks:
// the coinbase input is immature, so the whole TX — and every payment in it —
// is rejected by the network.
```
The same effect occurs if a `ReceivedOutput` deserialized via `ReceivedOutput::read` carries a non-existent or already-spent `OutPoint`: `multisig()` succeeds (offset↔script consistency is checked, not on-chain existence), `complete()` yields a transaction, and broadcast fails for all bundled payments.