### Title
Attacker overwrite of scanned outputs' data/origin across transactions in the same block - (File: processor/src/networks/bitcoin.rs)

### Summary

The external report describes a missing "consumed/updated" marker (`ownershipChange`) letting one asset's value be re-counted in a different context (`merge` writes value into NFT `_to` without marking it). The reachable analog in scope lives in `Bitcoin::get_outputs` in `processor/src/networks/bitcoin.rs:686-740`: a single `outputs` accumulator is shared across every transaction in a block, and for *each* matching transaction the code unconditionally rewrites `presumed_origin` and — for `OutputType::External` — `data` on **all** accumulated outputs, including outputs produced by earlier transactions. Any later transaction in the same block that pays to a Serai address silently re-binds the metadata of every earlier deposit in that block. This is the same root cause shape: an operation updates shared state without scoping/marking the update to the item it belongs to, so the effect "spills" onto unrelated resources an unprivileged sender can reach with a plain Bitcoin transaction.

### Finding Description

```rust
// processor/src/networks/bitcoin.rs:686-740
async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
  let (scanner, _, kinds) = scanner(key);

  let mut outputs = vec![];
  for tx in &block.txdata[1 ..] {
    for output in scanner.scan_transaction(tx) {
      ...
      outputs.push(output);
    }

    if outputs.is_empty() {
      continue;
    }

    let presumed_origin = { /* derived from tx.input[0] of THIS tx */ };
    let data = Self::extract_serai_data(tx);
    for output in &mut outputs {          // <-- iterates ALL accumulated outputs,
      if output.kind == OutputType::External {
        output.data.clone_from(&data);    //     not just this tx's outputs
      }
      output.presumed_origin.clone_from(&presumed_origin);
    }
  }
  outputs
}
```

`outputs` is declared outside the per-transaction loop and is never scoped to the current `tx`. When transaction B in a block contains any output matching a registered script (`External`, `Branch`, `Change`, `Forwarded`), the population loop at lines 731-736 overwrites `data` and `presumed_origin` on outputs already collected from transaction A — even though A and B are unrelated spends by unrelated users.

`data` for `OutputType::External` is `extract_serai_data(tx)` — the InInstruction embedded in the transaction that tells Serai how to handle the deposit (destination/refund metadata consumed downstream when the `Output` is emitted by `processor/src/multisigs/scanner.rs` and credited). `presumed_origin` is taken from `tx.input[0].previous_output` of the *last* matching transaction. So a later transaction's fields replace the earlier deposit's fields, while the earlier deposit's `ReceivedOutput` (the actual coins) is unchanged — the scanned output now pairs victim funds with attacker-controlled handling data.

### Impact Explanation

An unprivileged Bitcoin user can cause funds legitimately deposited to Serai to be reported with attacker-chosen `data` and `presumed_origin`. If `data` decodes to a valid InInstruction naming an attacker-controlled destination, the victim's coins are processed under the attacker's instruction — misrouting/hijacking of the deposit — or with garbage `data`, the deposit is received but bound to an invalid instruction, i.e., funds reported received that are not spendable/creditable as intended. The attacker only needs to land their transaction in the same block as the victim's, which is feasible by observing the mempool for transactions paying to the well-known, static Serai external/branch addresses.

### Likelihood Explanation

- Reachable entirely with public inputs: the attacker broadcasts an ordinary Bitcoin transaction paying ≥ dust to the Serai external address; no validator, RPC, or key-holder cooperation is needed.
- Requires same-block inclusion after the victim's transaction — trivially achievable via mempool observation and fee bumping.
- The scan path `get_outputs` → `scan_transaction` → `seen`/emit in `processor/src/multisigs/scanner.rs:550-640` processes these outputs unconditionally; there is no check that `data`/`presumed_origin` belong to the same tx as the output.
- Caveat: exact downstream handling of `Output.data` (InInstruction decoding and crediting) was not fully traced in this audit; severity scales with how much of the deposit flow the embedded data controls. At minimum this corrupts `presumed_origin` attribution and `data` binding for every earlier same-block deposit — a Medium-High integrity violation under the stated criteria.

### Recommendation

Scope the metadata population to the outputs of the current transaction only:

```rust
for tx in &block.txdata[1 ..] {
  let start = outputs.len();
  for output in scanner.scan_transaction(tx) { ...; outputs.push(output); }
  if outputs.len() == start { continue; }
  ...
  for output in &mut outputs[start ..] { ... }
}
```

or collect per-transaction `Vec`s and extend `outputs` after populating. Additionally, consider re-deriving `presumed_origin`/`data` from `output.tx_id()` rather than positional iteration, so the binding is structural rather than loop-order dependent.

### Proof of Concept

```rust
// Within Bitcoin::get_outputs over a block containing:
//   txA (victim): pays `external_address(key)` >= DUST, embeds victim InInstruction
//   txB (attacker, later in block): pays dust >= DUST to `external_address(key)`,
//                 embeds attacker InInstruction (or arbitrary bytes)
//
// Iteration for txA: outputs = [OutA], data = victim_data -> OutA.data = victim_data (correct)
// Iteration for txB: scan pushes OutB -> outputs = [OutA, OutB]
//   populate loop runs over BOTH:
//     OutA.data = attacker_data        // victim deposit re-bound
//     OutA.presumed_origin = attacker_origin
//     OutB.data = attacker_data
//
// Both outputs are then emitted by the Scanner (processor/src/multisigs/scanner.rs:562-640)
// with attacker-controlled handling data attached to the victim's coins.
```

The block-level `for tx` loop, shared `outputs` accumulator, and unscoped `for output in &mut outputs` population are directly visible at `processor/src/networks/bitcoin.rs:686-740`.