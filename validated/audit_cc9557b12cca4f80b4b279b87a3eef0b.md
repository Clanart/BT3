### Title
Unvalidated output provenance: any third-party payment to a registered offset script is misclassified as internal Branch/Change/Forwarded funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The Kibana advisory describes an Origin Validation Error: attacker-controlled "origin" metadata is trusted without verifying it against an expected source. The analog in `bitcoin-serai` is `Scanner::scan_transaction`/`get_outputs`: ownership and *kind* of a received output are determined solely by matching `script_pubkey` against the registered offset scripts, with no validation of where the funds actually came from. An arbitrary Bitcoin user can send a transaction paying directly to Serai's internal Branch, Change, or Forwarded addresses, and the processor will classify those outputs exactly as if they were internally produced.

### Finding Description
`Scanner::scan_transaction` matches outputs purely on `output.script_pubkey` membership in `self.scripts` and returns a `ReceivedOutput` carrying the associated scalar `offset` — nothing about the tx's inputs, origin, or context is checked (networks/bitcoin/src/wallet/mod.rs:199-214, [1](#0-0) ). `register_offset` maps each script to a fixed offset, so any payer to that script is indistinguishable from an internal transfer (networks/bitcoin/src/wallet/mod.rs:180-196, [2](#0-1) ). In `Bitcoin::get_outputs`, the `kind` is derived solely from the offset (`kinds[offset_repr_ref]`), and the `presumed_origin` is blindly taken from `tx.input[0]`'s spent output — fully attacker-controlled (processor/src/networks/bitcoin.rs:686-736, [3](#0-2) ). The `data` field (the InInstruction) is only populated for `OutputType::External`, meaning Branch/Change/Forwarded outputs skip the deposit-instruction requirement entirely (processor/src/networks/bitcoin.rs:730-734, [4](#0-3) ).

### Impact Explanation
Outputs intended to originate only from the protocol's own signing flow (Change, Branch for forwarded batches) can be injected by any unprivileged Bitcoin user who crafts a tx paying to those addresses. Such outputs are reported as received internal funds and enter the scheduler/wallet pipeline under the wrong `OutputType` and a forged `presumed_origin`. This can corrupt accounting (attacker-injected "change"), trigger unintended downstream handling of attacker-controlled inputs alongside real multisig UTXOs, and cause funds to be reported as received internal flows that bypass the External deposit checks (InInstruction data, dust rules applied per-kind).

### Likelihood Explanation
Reachable by any Bitcoin user: the Branch/Change/Forwarded scripts are deterministic (`hash_to_F(KEY_DST, b"branch"|"change"|"forward")` offsets of the group key), and the addresses are derivable once the group key is public. Sending a standard P2TR payment to them requires only a normal Bitcoin transaction — a public input reachable with no privileges.

### Recommendation
Restrict classification of Branch/Change/Forwarded outputs to transactions whose inputs are provably controlled by the multisig (e.g., require at least one input spend a known Serai script), or authenticate internal flows by matching on expected outpoints produced by prior protocol transactions rather than script alone. Treat any direct external payment to an internal script as External (or reject it) and never trust `tx.input[0]` as `presumed_origin` for non-External kinds.

### Proof of Concept
1. Observe the multisig group key `K`; compute `change_key = K + G*hash_to_F(KEY_DST, "change")` and its P2TR script via `p2tr_script_buf`.
2. Broadcast any ordinary Bitcoin tx paying ≥ dust to that script.
3. `Scanner::scan_transaction` returns a `ReceivedOutput` with the change offset; `get_outputs` labels it `OutputType::Change` with `presumed_origin` set to the attacker's own input address.
4. The output enters the scheduler as internal change, indistinguishable from protocol-produced change, despite never passing through the multisig signing flow — a forged-provenance deposit identical in shape to the forged-Origin-header bug class.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L180-196)
```rust
  pub fn register_offset(&mut self, mut offset: Scalar) -> Option<Scalar> {
    // This loop will terminate as soon as an even point is found, with any point having a ~50%
    // chance of being even
    // That means this should terminate within a very small amount of iterations
    loop {
      match p2tr_script_buf(self.key + (ProjectivePoint::GENERATOR * offset)) {
        Some(script) => {
          if self.scripts.contains_key(&script) {
            None?;
          }
          self.scripts.insert(script, offset);
          return Some(offset);
        }
        None => offset += Scalar::ONE,
      }
    }
  }
```

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

**File:** processor/src/networks/bitcoin.rs (L686-736)
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
```
