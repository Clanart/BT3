### Title
`SignableTransaction::new` accepts duplicate inputs, double-counting funds and producing an unspendable transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
The source report describes a missing "already claimed" check: `mintFromReserve` verifies a proof but never records that the entitlement was consumed, so the same tokenId can be claimed again after it is burned. The Serai analog is `SignableTransaction::new`, which accepts a caller-/attacker-influenced `Vec<ReceivedOutput>` as spendable inputs and sums their values into `input_sat` without ever checking that each input's `outpoint` is unique. A `ReceivedOutput` can be supplied twice (including via untrusted bytes through `ReceivedOutput::read`), causing the same UTXO to be credited twice — the analog of re-claiming the same tokenId.

### Finding Description
In `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`):

- `input_sat` is computed as a plain sum over `inputs` (`send.rs:175`) with no deduplication by `OutPoint`.
- `tx_ins` is built by mapping every entry in `inputs` to a `TxIn` spending `input.outpoint` (`send.rs:177-185`), again with no uniqueness check.
- Because `input_sat` is inflated, the `NotEnoughFunds` check at `send.rs:215` can pass when the transaction should be rejected, and the change computation at `send.rs:224-235` emits an oversized change output.
- The resulting `Transaction` contains two inputs spending the same prevout, which is consensus-invalid — it can never be mined. Every downstream consumer (`multisig`/`TransactionMachine`, `fee()`, `needed_fee()`) operates on this corrupted accounting.

`ReceivedOutput` is an explicitly untrusted-bytes type (`ReceivedOutput::read` is a listed parsing entry point), and neither it nor `Scanner` enforces anything at transaction-construction time; `scan_transaction` only guarantees uniqueness *within* one transaction's vout enumeration (`networks/bitcoin/src/wallet/mod.rs:199-214`), not across the `inputs` vector assembled by the caller.

### Impact Explanation
- **Unspendable signed transaction / locked signing round**: the multisig will happily FROST-sign the malformed transaction. It can never confirm, so the spend plan it encodes is dead — the payments never occur and the consumed inputs can't be used in that plan. For a threshold wallet where signing ceremonies are coordinated and expensive, a permanently-invalid-but-validly-signed transaction is a meaningful liveness/funds-availability failure ("funds reported received that are not spendable" — the change output is computed and committed as if it were real).
- **Corrupted fee/change accounting**: `fee()` (`send.rs:138-141`) reports a fee based on doubled input value, and the change output (`send.rs:230`) is inflated by the duplicated amount, so any accounting built on these values is wrong.
- Mirroring the source finding: the same asset (prevout) is presented twice and credited twice because no consumed/claimed tracking exists at construction.

### Likelihood Explanation
Reachable whenever the `inputs` list is assembled from data an unprivileged party can influence — e.g., outputs parsed via `ReceivedOutput::read` from coordinator-supplied or serialized data, or an honest-but-naive aggregator merging scanner results. The attacker does not need to create real on-chain duplicates: they only need the *same* `ReceivedOutput` value to appear twice in the vector. The library performs zero validation of `outpoint` uniqueness (no `HashSet` of outpoints anywhere in `new`), so the check-free path is deterministic. Exploitation yields a guaranteed-invalid transaction rather than probabilistic behavior.

### Recommendation
In `SignableTransaction::new`, track seen outpoints and reject duplicates, e.g.:

```rust
let mut seen = HashSet::with_capacity(inputs.len());
for input in &inputs {
  if !seen.insert(input.outpoint) {
    Err(TransactionError::DuplicateInput)?; // new variant
  }
}
```

This is the direct analog of the recommended `isClaimed` mapping: record consumption of each `OutPoint` before crediting it. Optionally also assert `prevouts[i].script_pubkey` consistency is later enforced by `multisig` (it is), but uniqueness must be enforced at construction since `input_sat` and change are computed before any signature exists.

### Proof of Concept
```rust
#[test]
fn double_counted_input() {
  let (keys, key) = keys(); // existing test helper
  let mut scanner = Scanner::new(key).unwrap();
  let output = send_and_get_output(&rpc, &scanner, key).await; // one real UTXO, value V

  // Present the same ReceivedOutput twice — mimics re-claiming a consumed entitlement.
  // Equivalent bytes can be produced via ReceivedOutput::read on duplicated data.
  let payments = [(p2tr_script_buf(key).unwrap(), 1005)];

  let tx = SignableTransaction::new(
    vec![output.clone(), output], // same outpoint twice
    &payments,
    Some(change_addr),
    None,
    FEE,
  ).unwrap();

  // input_sat is 2*V: the balance check passed on phantom funds
  // tx.input contains two TxIns spending the same OutPoint
  assert_eq!(tx.input[0].previous_output, tx.input[1].previous_output);

  // The transaction is consensus-invalid: it can be FROST-signed yet never confirmed,
  // while change/fee accounting assumed double the real input value.
}
```

**Caveat**: severity is contingent on how `inputs` are sourced by integrators; if callers only pass scanner-derived unique outputs, this is robustness-only. Within the stated rules, `ReceivedOutput::read` is an enumerated untrusted-bytes entry point, and the constructor is the only place uniqueness could be enforced — matching the missing-claim-check bug class.