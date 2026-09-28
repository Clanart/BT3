### Title
Attacker-controlled transaction data overwrites `data`/`presumed_origin` of unrelated deposits in the same block - (File: processor/src/networks/bitcoin.rs)

### Summary
CVE-2011-4125 is an untrusted-search-path bug: unprivileged input silently determines what a privileged component loads/executes. The Serai analog is in `Bitcoin::get_outputs`: the per-transaction `outputs` accumulator and the `if outputs.is_empty() { continue }` guard are scoped to the whole block, not to each transaction. An attacker's transaction that contains **no** output paying Serai still triggers the `presumed_origin`/`extract_serai_data` pass, and its results are written into `ReceivedOutput`s belonging to *earlier, unrelated transactions* in the same block.

### Finding Description
In `get_outputs`, `outputs` is declared once before iterating `block.txdata[1 ..]`. For every tx, matching outputs are pushed onto the shared vector; then the guard `if outputs.is_empty() { continue }` is evaluated against the *cumulative* vector, not the current tx's matches. Once any earlier tx produced an output, every subsequent tx — including attacker transactions with zero Serai outputs — proceeds to fetch `tx.input[0]`'s origin and compute `Self::extract_serai_data(tx)`, then loops `for output in &mut outputs` assigning `output.data` (for `OutputType::External`) and `output.presumed_origin`. [1](#0-0) 

`data` on an `External` output carries the user's `InInstruction` (e.g., `Shorthand::transfer` specifying the Serai destination account) extracted via `extract_serai_data`. The scanner in `processor/src/multisigs/scanner.rs:562-567` pushes these `Output`s into credited-deposit processing (`ScannerEvent::Block`), so `output.data` determines who is minted funds for the deposit.

### Impact Explanation
An attacker who observes a victim's deposit tx in the mempool can submit their own transaction — paying no Serai-registered `script_pubkey`, but embedding a crafted InInstruction naming the attacker's Serai address — ordered after the victim's tx in the same block. The loop at `processor/src/networks/bitcoin.rs:731-736` then overwrites `data` on the victim's `External` output with the attacker's data (and `presumed_origin` with the attacker's input-0 source). The victim's deposited BTC is credited to the attacker — theft of bridged funds, reachable purely from a public unconfirmed/mined Bitcoin transaction, matching the CVE's pattern of unprivileged input substituting what the privileged path acts on.

### Likelihood Explanation
Requires inclusion ordering after the victim's tx in a scanned, confirmed block — achievable via normal fee bidding/miner ordering, and the attacker controls the tx content fully. No key material, validator collusion, or RPC access needed; only a Bitcoin transaction. The vulnerability is deterministic once ordering is achieved (the guard simply fails to skip).

### Recommendation
Track per-transaction matches: collect the current tx's scanned outputs into a fresh vector, `continue` when that vector is empty, and only then compute `presumed_origin`/`data` and assign to that tx's outputs. Optionally also gate the `presumed_origin` fetch on the tx actually producing outputs, and consider verifying `extract_serai_data` only applies to txs spending to an External address.

### Proof of Concept
```rust
// Block contains, in order:
//   txdata[1] = victim deposit tx: output[0].script_pubkey == serai external address,
//               with InInstruction data crediting victim_serai_addr
//   txdata[2] = attacker tx: no Serai outputs, input[0] = attacker's utxo,
//               embeds InInstruction crediting attacker_serai_addr
//
// get_outputs(block, key):
//   txdata[1]: outputs = [victim_output(data = victim_data)]  // push
//              outputs.is_empty() == false -> origin/data pass, data = victim's (correct so far)
//   txdata[2]: scan_transaction -> nothing pushed
//              outputs.is_empty() == false (victim_output still in vec!) -> 
//              extract_serai_data(attacker_tx) -> attacker_data
//              victim_output.data = attacker_data  // OVERWRITTEN
//
// Result: ScannerEvent::Block emits victim's BTC output carrying attacker's
// InInstruction -> attacker is credited for the victim's deposit.
```

### Citations

**File:** processor/src/networks/bitcoin.rs (L686-740)
```rust
  async fn get_outputs(&self, block: &Self::Block, key: ProjectivePoint) -> Vec<Output> {
    let (scanner, _, kinds) = scanner(key);

    let mut outputs = vec![];
    // Skip the coinbase transaction which is burdened by maturity
    for tx in &block.txdata[1 ..] {
      for output in scanner.scan_transaction(tx) {
        let offset_repr = output.offset().to_repr();
        let offset_repr_ref: &[u8] = offset_repr.as_ref();
        let kind = kinds[offset_repr_ref];

        let output = Output { kind, presumed_origin: None, output, data: vec![] };
        assert_eq!(output.tx_id(), tx.id());
        outputs.push(output);
      }

      if outputs.is_empty() {
        continue;
      }

      // populate the outputs with the origin and data
      let presumed_origin = {
        // This may identify the P2WSH output *embedding the InInstruction* as the origin, which
        // would be a bit trickier to spend that a traditional output...
        // There's no risk of the InInstruction going missing as it'd already be on-chain though
        // We *could* parse out the script *without the InInstruction prefix* and declare that the
        // origin
        // TODO
        let spent_output = {
          let input = &tx.input[0];
          let mut spent_tx = input.previous_output.txid.as_raw_hash().to_byte_array();
          spent_tx.reverse();
          let mut tx;
          while {
            tx = self.rpc.get_transaction(&spent_tx).await;
            tx.is_err()
          } {
            log::error!("couldn't get transaction from bitcoin node: {tx:?}");
            sleep(Duration::from_secs(5)).await;
          }
          tx.unwrap().output.swap_remove(usize::try_from(input.previous_output.vout).unwrap())
        };
        Address::new(spent_output.script_pubkey)
      };
      let data = Self::extract_serai_data(tx);
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
      }
    }

    outputs
  }
```
