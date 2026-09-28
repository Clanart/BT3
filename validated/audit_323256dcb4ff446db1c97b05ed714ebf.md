### Title
Scanner attaches a later transaction's origin and overwrites instruction data on already-collected outputs, ignoring the output-kind flag — (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::get_outputs` accumulates `ReceivedOutput`s across all transactions in a block, but the metadata-population loop that follows runs over the *cumulative* `outputs` vector, not just the current transaction's outputs. The `OutputType::External` "flag" is only checked for `data`; `presumed_origin` is stamped unconditionally, and `data` on earlier `External` outputs is overwritten by subsequent transactions. An unprivileged party who gets any transaction paying a Serai-scanned script into a block can misattribute origins and clobber attached InInstruction data on outputs belonging to other transactions.

### Finding Description
The analogous flag here is `output.kind == OutputType::External`, the check meant to decide which outputs may receive transaction-derived data (`extract_serai_data`, the Bitcoin InInstruction commitment) and a sender-derived `presumed_origin` (from `tx.input[0]`'s spent output script).

```rust
// processor/src/networks/bitcoin.rs:691-736
for tx in &block.txdata[1 ..] {
  for output in scanner.scan_transaction(tx) {
    ...
    outputs.push(output);          // accumulates across txs
  }
  if outputs.is_empty() { continue; }   // wrong predicate: cumulative, not per-tx
  let presumed_origin = { ... tx.input[0] ... };
  let data = Self::extract_serai_data(tx);
  for output in &mut outputs {            // iterates ALL prior outputs
    if output.kind == OutputType::External {
      output.data.clone_from(&data);     // overwrites earlier txs' data
    }
    output.presumed_origin.clone_from(&presumed_origin); // no kind check at all
  }
}
```

Two defects:

1. `outputs.is_empty()` is false once any earlier transaction in the block produced a scanned output, so the population loop executes for every subsequent transaction — including ones that produced no scanned outputs themselves.
2. `presumed_origin` is assigned to every accumulated output with no `kind` check, so `Branch`, `Change`, and `Forwarded` outputs (and outputs from other transactions) receive an arbitrary origin derived from an unrelated transaction's `input[0]`.

### Impact Explanation
- An attacker's second transaction in the same block (with any scanned output, e.g., a dust payment to the External address) causes `extract_serai_data` of *that* transaction to be written over the `data` of an earlier `External` output — erasing or replacing the victim's InInstruction (e.g., a `transfer` destination). Funds arrive but are processed with the wrong/no instruction, leading to misrouting or unclaimable attribution.
- Internal `Change`/`Branch`/`Forwarded` outputs, and outputs from unrelated earlier transactions, get `presumed_origin` set to an attacker-controlled address. `presumed_origin` is exported via `Output::presumed_origin()` and serialized in `Output::write`, so downstream refund/attribution logic that trusts it can credit the attacker as the depositor of the protocol's own change or another party's output.
- A transaction with no prior inputs retrievable via RPC causes a 5-second retry loop (`while tx.is_err()`), but that is secondary.

### Likelihood Explanation
An attacker needs only to get a transaction with a scanned-output payment into the same block as a target transaction — ordinary, unprivileged Bitcoin usage. No collusion, key material, or validator status is required; block ordering of the two transactions is the only condition, and attackers can submit to miners or wait for natural co-block inclusion.

### Recommendation
- Populate origin/data only over the outputs scanned from the *current* transaction (e.g., use a per-transaction `Vec` or slice the tail of `outputs`).
- Apply the `kind == OutputType::External` check to `presumed_origin` as well, or explicitly decide and document which kinds may carry an origin.

### Proof of Concept
In a regtest block: tx1 pays the Serai external address with an InInstruction (External output O1). tx2 (attacker) pays any Serai-scanned address with no InInstruction and input[0] spending an attacker-controlled P2PKH output. `get_outputs` returns O1 with `data` overwritten by tx2's (empty/malicious) extracted data, and the change/branch output of a protocol transaction (if present earlier in the block) plus O1 get `presumed_origin = attacker's address`. This is observable purely from public on-chain inputs to `get_outputs`.

Note: I was unable to fully trace `presumed_origin`'s downstream consumption in `processor/src/multisigs` (grep output did not expose the use sites), so the refund/crediting impact is inferred from the field's documented purpose rather than confirmed end-to-end; the data-overwrite and missing kind-gate in `get_outputs` are confirmed directly from the code.