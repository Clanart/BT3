### Title
Duplicate `ReceivedOutput` inputs double-count funds and produce an unbroadcastable transaction - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` never checks that the supplied `inputs` are distinct outpoints. Passing the same `ReceivedOutput` twice causes its value to be summed twice into `input_sat` while the same prevout is emitted twice into `tx.input`. The result is a signed transaction that Bitcoin consensus rejects (duplicate inputs), with payment/change/fee accounting computed against value that does not exist. This is the Serai-shaped analog of CVE-2017-15186's double-free: the same underlying object (a UTXO) is used twice, corrupting value accounting.

### Finding Description
In `SignableTransaction::new`, `input_sat` sums `input.output.value` over all provided inputs and `tx_ins` is built by mapping each input's `outpoint` verbatim, with no deduplication:

```rust
// networks/bitcoin/src/wallet/send.rs:175-185
let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
let tx_ins = inputs
  .iter()
  .map(|input| TxIn { previous_output: input.outpoint, ... })
  .collect::<Vec<_>>();
```

The solvency check `input_sat < payment_sat + needed_fee` and the change calculation `input_sat - payment_sat - fee_with_change` therefore treat a duplicated output as fresh money. `multisig()` only checks that `p2tr_script_buf(offset.group_key()) == prevouts[i].script_pubkey`, which a byte-for-byte copy of a genuine `ReceivedOutput` satisfies. `TransactionSignMachine::sign` then signs each duplicated input with `Prevouts::All` and `complete()` emits a fully-signed `Transaction` whose two inputs reference the same outpoint — invalid under Bitcoin consensus, so it can never confirm.

### Impact Explanation
An unprivileged party who can feed `ReceivedOutput` bytes into the wallet path (`ReceivedOutput::read` is an untrusted-bytes entry point, and Serai's own scanner explicitly "MAY fire the same event multiple times") can cause the multisig to:

1. Pass the `NotEnoughFunds` check with phantom value, producing a change output or paying a fee computed on funds that don't exist.
2. Produce a signed transaction that is permanently unconfirmable, while the coordinator believes the payments were sent — funds credited as spent/forwarded are not actually moved, and the wallet's accounting diverges from the chain.

Additionally, `sum::<u64>()` over attacker-influenced `ReceivedOutput` values can overflow and panic, a pure DoS. The primary impact is a signed, consensus-invalid transaction and misaccounted change/fees — a medium-severity integrity/availability failure.

### Likelihood Explanation
Triggering requires a duplicated `ReceivedOutput` to reach `SignableTransaction::new`. Since `ReceivedOutput::read` accepts attacker-controlled bytes and downstream consumers (processors acting on scanner events which may repeat) reconstruct inputs from serialized outputs, a duplicated entry is a realistic input shape rather than a caller-bug-only scenario. The prevout must reference a real wallet-controlled output for `multisig()` to accept it, which limits but does not eliminate exploitability: copying a legitimately scanned output is trivial.

### Recommendation
- In `SignableTransaction::new`, reject duplicate `OutPoint`s (e.g., insert into a `HashSet` and error on collision) before computing `input_sat`.
- Use `checked_add`/`checked_sum` for `input_sat` and `payment_sat` to prevent `u64` overflow panics on crafted inputs.
- Optionally have `Scanner::scan_transaction`/consumers dedup received outpoints defensively, since the scanner contract permits repeated events.

### Proof of Concept
```rust
// Given a genuine ReceivedOutput `o` scanned by the wallet:
let bytes = o.serialize();
let dup = ReceivedOutput::read(&mut bytes.as_slice()).unwrap(); // identical copy

let tx = SignableTransaction::new(
  vec![o.clone(), dup],           // same outpoint twice
  &[(payment_script, large_payment)], // payment_sat near input_sat
  Some(change_script),
  None,
  fee_per_vbyte,
).unwrap();
// input_sat == 2 * o.value, so payments/change pass NotEnoughFunds
// tx.transaction().input contains two TxIns with identical previous_output
// After multisig + FROST signing, the final Transaction is consensus-invalid
// (Bitcoin rejects duplicate inputs) yet change was computed on double value.
```