### Title
`get_outputs` attributes the last transaction's origin and InInstruction data to every output found in the block - (File: networks/bitcoin/src/processor-side handling in `processor/src/networks/bitcoin.rs`)

### Summary
The external report describes a counter (`roundDepositCount`) that is silently over-counted because an extra, optional input (`msg.value`) is added on top of the real items being counted, corrupting downstream accounting. The same bug class exists in Serai's Bitcoin network adapter: `Bitcoin::get_outputs` accumulates `outputs` in a vector declared *outside* the per-transaction loop, then unconditionally applies the *current* transaction's `presumed_origin` and Serai `data` to **all** accumulated outputs — including outputs from earlier transactions in the same block. An attacker who pays a Serai multisig address controls which transaction's data/origin lands on unrelated outputs.

### Finding Description
In `processor/src/networks/bitcoin.rs`, `get_outputs` creates `let mut outputs = vec![];` once (line 689) and iterates `for tx in &block.txdata[1 ..]` (line 691). For each transaction it pushes newly scanned `Output`s into the shared `outputs` vector (lines 692–700). It then checks `if outputs.is_empty() { continue }` (line 702) — which is true whenever *any* earlier tx produced an output — and computes `presumed_origin` from `tx.input[0]` (lines 714–729) and `data` from `Self::extract_serai_data(tx)` (line 730). Finally it loops `for output in &mut outputs` (line 731) and assigns `output.data` and `output.presumed_origin` to **every** output in the vector, not just the outputs scanned from the current `tx` (lines 731–736).

The effect: after the loop, every `Output` reported for the block carries the `presumed_origin` and `data` of the *last* transaction in the block that contained a relevant output (the assignments are overwritten each iteration). Earlier transactions' own origin/data are never correctly attached.

`presumed_origin` and `data` are not decorative: downstream, `instruction_from_output` and the multisig scanning path (`processor/src/multisigs/mod.rs`, `scanner_event_to_multisig_event`) turn `Output.data` on `OutputType::External` outputs into `InInstruction`s and use `presumed_origin` for refund routing. A mismatched `data` field therefore changes what instruction Serai executes for funds that were actually sent by a different transaction.

### Impact Explanation
An unprivileged Bitcoin user can craft a block's worth of transactions so that a legitimate deposit to a Serai multisig (large amount, no/innocent data, placed earlier in the block) is reported by `get_outputs` with the `data` (InInstruction) and `presumed_origin` of a *different*, attacker-chosen transaction placed later in the same block (e.g., a dust payment to the same external address carrying a crafted `InInstruction`). Consequences include:

- An honest depositor's funds being processed under an attacker-supplied `InInstruction` (e.g., a `Transfer` to an attacker `SeraiAddress`, or a `refund_to` pointing at the attacker's address), effectively redirecting how the deposit is credited/handled.
- `presumed_origin` misattribution corrupts refund paths, since refund `PlanFromScanning` construction consumes the origin implied by the scanned output.

This is "funds processed under an instruction they didn't carry" — a concrete integrity failure of the deposit pipeline, not merely cosmetic.

### Likelihood Explanation
Exploitation requires only that an attacker get their dust transaction into the same block after a victim's deposit transaction and that both pay a Serai-scanned address. Attackers observe the mempool and can broadcast/bribe for ordering; paying an already-known external multisig address is permissionless. No validator, peer, or key compromise is needed. The misattribution is deterministic — `&mut outputs` always covers the whole accumulated vector — so any block containing ≥2 transactions paying scanned addresses misattributes data with probability 1 (whenever more than one distinct origin/data exists).

### Recommendation
Scope the per-transaction post-processing to only the outputs scanned from the current `tx`. Either collect into a per-tx vector and extend the outer one, or slice by index:

```rust
for tx in &block.txdata[1 ..] {
  let start = outputs.len();
  for output in scanner.scan_transaction(tx) {
    /* push as today */
  }
  if outputs.len() == start { continue; }
  // compute presumed_origin and data for THIS tx
  for output in &mut outputs[start ..] {
    /* assign data/presumed_origin */
  }
}
```

Also note the `if outputs.is_empty() { continue }` check at line 702 currently triggers `get_transaction` lookups on `tx.input[0]` even when the *current* tx contributed no outputs (as long as an earlier tx did) — the index-based fix resolves this too.

### Proof of Concept
Conceptual Rust trace through `Bitcoin::get_outputs` (`processor/src/networks/bitcoin.rs:686-740`):

```rust
// Block contains:
//   txA (position 1): pays external_address(key) 1.0 BTC, no Serai data
//   txB (position 2): pays external_address(key) dust, OP_RETURN data = InInstruction::Transfer(attacker_addr)

let mut outputs = vec![];
for tx in &block.txdata[1 ..] {
  for output in scanner.scan_transaction(tx) { outputs.push(output); }
  if outputs.is_empty() { continue; }      // false for txB (txA already pushed)

  let presumed_origin = /* tx.input[0]'s origin */;   // txB's origin during 2nd iter
  let data = Self::extract_serai_data(tx);            // txB's attacker data during 2nd iter
  for output in &mut outputs {                        // iterates txA's output AND txB's
    if output.kind == OutputType::External {
      output.data.clone_from(&data);                  // txA's 1 BTC now carries txB's InInstruction
    }
    output.presumed_origin.clone_from(&presumed_origin); // txA's output now "from" txB's sender
  }
}
```

Result: `outputs[0]` (the 1 BTC deposit from txA) is returned with `data == InInstruction::Transfer(attacker_addr)` and `presumed_origin == txB` origin. When `scanner_event_to_multisig_event` processes it (`processor/src/multisigs/mod.rs:934-957`), `instruction_from_output` decodes the attacker's instruction and pushes it as if the depositor authored it.