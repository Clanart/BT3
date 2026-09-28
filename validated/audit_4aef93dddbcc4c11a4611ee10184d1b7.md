Let me verify the suspected data-attribution bug in `get_outputs`.### Title
Attacker-controlled transaction injects its embedded `InInstruction` data and origin onto other transactions' outputs in the same block - ([File: processor/src/networks/bitcoin.rs])

### Summary
`Bitcoin::get_outputs` declares `outputs` outside the per-transaction loop and uses `outputs.is_empty()` (rather than "this tx produced outputs") as the guard for the origin/data population pass. The population loop then iterates over **all** accumulated `outputs`, not just the ones scanned from the current `tx`. Any later transaction in the same block — including one paying Serai nothing, and fully controlled by an attacker — causes `extract_serai_data(tx)` and the `tx.input[0]`-derived `presumed_origin` to be stamped onto outputs belonging to other transactions. This is the direct analog of the CVE's "trailing data smuggled through a routing path": attacker bytes cross a context boundary and are attributed to a victim's deposit.

### Finding Description
In `get_outputs` (`processor/src/networks/bitcoin.rs:686-740`):

- `let mut outputs = vec![]` is hoisted above `for tx in &block.txdata[1 ..]` (lines 689-691).
- `for output in scanner.scan_transaction(tx)` pushes into that shared vec (692-700).
- `if outputs.is_empty() { continue }` (702-704) is false as soon as *any* earlier tx in the block produced a Serai output — so the following population pass runs for every subsequent tx in the block, even ones with zero matching outputs.
- `for output in &mut outputs` (731-736) then sets `output.data` (for `OutputType::External`) to `Self::extract_serai_data(tx)` — the OP_RETURN push or the witness `InInstruction` slot (`extract_serai_data`, lines 493-519) — and overwrites `presumed_origin` with an address derived from `tx.input[0]`'s spent output (707-729).

So the last matching/non-matching tx in a block "wins" and rewrites the `data`/`presumed_origin` of outputs from earlier txs.

### Impact Explanation
`output.data` is the Serai `InInstruction` payload associated with a deposit (the same data path exercised by the `literal` test which embeds an instruction in a P2WSH witness). An attacker who mines or relays a crafted transaction into the same block as a victim's deposit can attach an arbitrary instruction to the victim's `External` output — e.g., redirecting credit/forwarding semantics to the attacker — while `presumed_origin` is simultaneously falsified to the attacker's input. The victim's funds are then processed under attacker-supplied metadata, satisfying "funds reported received" under forged instruction data / an injection of untrusted bytes across a trust boundary. Reachable by any unprivileged party that can get a transaction into a block containing a Serai-bound deposit.

### Likelihood Explanation
Requires only that the attacker's tx land in the same block as a victim deposit and be ordered after it (or even before/after arbitrarily, since any tx with `outputs` non-empty triggers a re-stamp of all accumulated outputs). Block ordering is influenceable via fees/mining; the victim only needs to deposit to the public external address. Cost is one ordinary Bitcoin transaction.

### Recommendation
Scope `outputs` per transaction: declare the vec inside the `for tx` loop (or track `let tx_outputs_start = outputs.len()` and only populate `outputs[tx_outputs_start..]`), and `continue` when the *current* tx produced no outputs. Additionally, only populate `data` when `extract_serai_data(tx)` is non-empty, and validate `tx.input` is non-empty before indexing `tx.input[0]`.

### Proof of Concept
```rust
// Block contains two non-coinbase transactions:
//   tx_v: victim deposit -> Serai external address, no OP_RETURN/witness data
//   tx_a: attacker tx with NO Serai outputs, but whose witness embeds a
//         segwit_data_pattern redeem script carrying attacker InInstruction bytes
let outputs = bitcoin.get_outputs(&block, group_key).await;
// Expected: outputs[0].data == [] and presumed_origin == victim's origin
// Actual:   outputs[0].data == attacker InInstruction from tx_a,
//           outputs[0].presumed_origin == address of tx_a.input[0]'s spent output
// because `outputs` is non-empty when tx_a is processed, so the population
// pass rewrites every accumulated output with tx_a's data and origin.
``` [1](#0-0) [2](#0-1)

### Citations

**File:** processor/src/networks/bitcoin.rs (L493-519)
```rust
  fn extract_serai_data(tx: &Transaction) -> Vec<u8> {
    // check outputs
    let mut data = (|| {
      for output in &tx.output {
        if output.script_pubkey.is_op_return() {
          match output.script_pubkey.instructions_minimal().last() {
            Some(Ok(Instruction::PushBytes(data))) => return data.as_bytes().to_vec(),
            _ => continue,
          }
        }
      }
      vec![]
    })();

    // check inputs
    if data.is_empty() {
      for input in &tx.input {
        let witness = input.witness.to_vec();
        // expected witness at least has to have 2 items, msg and the redeem script.
        if witness.len() >= 2 {
          let redeem_script = ScriptBuf::from_bytes(witness.last().unwrap().clone());
          if Self::segwit_data_pattern(&redeem_script) == Some(true) {
            data.clone_from(&witness[witness.len() - 2]); // len() - 1 is the redeem_script
            break;
          }
        }
      }
```

**File:** processor/src/networks/bitcoin.rs (L686-737)
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
```
