### Title
Unauthorized deposits to internal offset scripts are misclassified as Branch/Change/Forwarded outputs - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` attributes outputs to registered key offsets purely by `script_pubkey` match, with no check on who created the funding transaction. Any external party who learns one of Serai's internal offset scripts (Branch, Change, or Forwarded addresses, which are publicly visible on-chain once used) can pay directly to it, and the output will be reported with the internal `OutputType` rather than `External`. This mirrors the CVE-2025-6171 pattern: a resource (an internal output type) is reachable without the authorization step that was assumed to gate it.

### Finding Description
`Scanner` keeps a `HashMap<ScriptBuf, Scalar>` of all registered scripts — the base key script plus every script derived via `register_offset` — and `scan_transaction` returns a `ReceivedOutput` for any transaction output whose `script_pubkey` is in that map, with the stored offset attached. There is no provenance check: any on-chain payment to a known script is accepted [1](#0-0) .

In the processor, `Bitcoin::get_outputs` maps the scanned offset back to an `OutputType` via `kinds[offset_repr_ref]`, so a payment to a Branch/Change/Forwarded script is emitted as `Output { kind: Branch | Change | Forwarded, .. }` [2](#0-1) . The code even acknowledges address reuse is possible — it unconditionally pulls `presumed_origin` from `tx.input[0]` and attaches Serai instruction `data` only for `OutputType::External` [3](#0-2) .

The offsets themselves are per-kind (`scanner(key)` derives distinct offsets for `External`, `Branch`, `Change`, `Forwarded`), and the downstream scheduler treats non-`External` kinds as outputs Serai itself created — branch outputs feed internal plans, forwarded outputs feed the forward flow. An attacker-controlled deposit arriving with kind `Branch`/`Change`/`Forwarded` bypasses the implicit authorization assumption that only Serai-originated transactions can produce such outputs.

### Impact Explanation
- Funds sent by a third party to a leaked Branch/Forwarded script are reported to the scheduler as internally-created outputs. This corrupts output classification: an `External` deposit that should carry an `InInstruction` and mint sri-tokens is instead treated as a protocol-internal UTXO (or vice versa, an attacker can inject outputs into internal handling paths that assume Serai authorship of the funding transaction).
- Since `data` (the InInstruction) is only attached to `External` outputs, attacker deposits to internal scripts silently drop any instruction and may be consumed as inputs to scheduled transactions, letting an attacker influence which UTXOs fund internal plans.
- This is a funds-accounting / misattribution issue, matching the "funds reported received that are not [correctly] spendable/classifiable" acceptance criterion.

### Likelihood Explanation
Any on-chain observer can extract the internal scripts: every Branch, Change, and Forwarded script_pubkey appears in Serai's own confirmed transactions. Sending a standard P2TR payment to one costs only the output value plus fees — no cryptographic break, no validator collusion, no authenticated access required. The only mitigation is that the attacker's funds become part of the multisig's UTXO set, so the attack costs real money; damage is bounded to classification/accounting confusion rather than direct theft, placing this at Medium.

### Recommendation
- In `get_outputs`, only emit `Branch`/`Change`/`Forwarded` outputs when the funding transaction is one Serai itself produced (e.g., check `tx.input[0].previous_output` belongs to a known multisig output, or maintain a set of expected internal txids), and treat unexpected third-party payments to internal scripts as `External` or ignore them.
- Alternatively, reserve a never-published derivation path for internal scripts so their script_pubkeys are not learnable before first use, and refuse to classify outputs on scripts whose first-seen funder is external.

### Proof of Concept
1. Serai executes any transaction producing a Change or Branch output; the attacker reads `p2tr` script_pubkey `S` (=`key + G*offset_internal`) from the confirmed transaction.
2. Attacker broadcasts a transaction paying `S` an amount ≥ `N::DUST`, with no InInstruction.
3. `Scanner::scan_transaction` matches `S` and returns a `ReceivedOutput` with `offset == offset_internal` [4](#0-3) .
4. `Bitcoin::get_outputs` looks up `kinds[offset_internal]` and emits `Output { kind: OutputType::Branch (or Change/Forwarded), .. }`, and the processor DB records it as an internally-typed output despite the attacker having no authorization to create one [5](#0-4) .

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L199-214)
```rust
  pub fn scan_transaction(&self, tx: &Transaction) -> Vec<ReceivedOutput> {
    let mut res = Vec::new();
    for (vout, output) in tx.output.iter().enumerate() {
      // If the vout index exceeds 2**32, stop scanning outputs
      let Ok(vout) = u32::try_from(vout) else { break };

      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
    }
    res
  }
```

**File:** processor/src/networks/bitcoin.rs (L686-700)
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
```

**File:** processor/src/networks/bitcoin.rs (L706-736)
```rust
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
```
