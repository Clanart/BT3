### Title
Cross-transaction deposit data/origin overwrite in `Bitcoin::get_outputs` lets any payer rewrite the InInstruction data and presumed origin of every other Serai-bound output in the same block - (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::get_outputs` declares the `outputs` vector outside the per-transaction loop and then applies the *current* transaction's `presumed_origin` and `extract_serai_data` result to **all** outputs accumulated so far, not just the ones produced by that transaction. An attacker who includes a transaction paying any registered Serai script in the same block as a victim deposit can overwrite the victim output's `data` (the Serai InInstruction payload carried by the deposit) and `presumed_origin` with attacker-controlled values. This is the analog of the "fake deposit credited" class: deposit metadata that should be bound to the paying transaction is silently replaced by data from an unrelated transaction.

### Finding Description
In `get_outputs`, `outputs` is initialized once before iterating `block.txdata[1 ..]`. For each transaction, newly scanned outputs are pushed onto the shared vector. Then, if `outputs` is non-empty, the code computes `presumed_origin` from `tx.input[0]`'s spent output and `data` from `extract_serai_data(tx)`, and iterates `for output in &mut outputs` — mutating every previously collected output, including those from earlier transactions. [1](#0-0) 

The bug is a loop-scoping error: the inner mutation loop runs over the accumulated vector rather than over only the outputs scanned from the current `tx`. `extract_serai_data` pulls attacker-controlled bytes from the transaction's OP_RETURN output or from a `SHA256 <msg> EQUALVERIFY` witness redeem script in its inputs, and `presumed_origin` is derived from the first input's prevout script. [2](#0-1) 

The corrupted `data` is exactly the field exposed via `OutputTrait::data()` and serialized in `Output::write`, i.e., the per-output metadata the processor uses to interpret deposits. [3](#0-2) 

### Impact Explanation
Any unprivileged party can send a Bitcoin transaction paying ≥ `DUST` (10,000 sats) to a Serai `External`/`Branch`/`Change`/`Forwarded` script — no special access needed. If their transaction lands in the same block after a victim deposit, the victim output's `data` (which carries the deposit's InInstruction, e.g., destination/refund information) is replaced with the attacker's bytes, or cleared if the attacker's tx carries no data, and its `presumed_origin` is reassigned to the attacker's prevout address. Deposits are therefore processed with metadata that does not belong to the depositing transaction, which can misdirect or corrupt how the deposit is accounted/credited downstream. The cost is one dust-sized output; every earlier Serai-bound output in the block is affected.

### Likelihood Explanation
Requires only that the attacker's transaction appear in the same block as a victim's deposit, ordered after it — trivially achievable by watching the mempool or simply spraying payments. Blocks frequently contain multiple transactions to the same bridge address. The overwrite is deterministic once ordering holds; no race or privileged position is needed.

### Recommendation
Bind origin and data per-transaction: collect the outputs scanned from the current `tx` into a fresh slice (or track the starting index) and only mutate those. E.g., record `let start = outputs.len()` before scanning the tx, then iterate `outputs[start ..]` when assigning `presumed_origin` and `data`. Alternatively, produce a per-tx `Vec<Output>` and `extend` the block-level vector after enrichment.

### Proof of Concept
1. Victim broadcasts `tx_v` paying the Serai external address with OP_RETURN data `d_v` encoding their InInstruction; the tx's first input spends an output at address `a_v`.
2. Attacker broadcasts `tx_a` paying ≥ dust to any registered Serai script for the same key, with witness/OP_RETURN data `d_a` of their choosing (or none) and first input prevout address `a_a`.
3. Both confirm in one block with `tx_a` after `tx_v` in `txdata`.
4. `get_outputs` processes `tx_v`: pushes `O_v`, sets `O_v.data = d_v`, `O_v.presumed_origin = a_v`. It then processes `tx_a`: pushes `O_a`, and the `for output in &mut outputs` loop sets `O_v.data = d_a` (or `[]`) and `O_v.presumed_origin = a_a`, overwriting the victim's values.
5. The scanner emits `O_v` with the attacker's data/origin; the deposit is processed under corrupted metadata while the underlying coins are real — mirroring the fake-deposit-credit class.

### Citations

**File:** processor/src/networks/bitcoin.rs (L132-143)
```rust
  fn data(&self) -> &[u8] {
    &self.data
  }

  fn write<W: io::Write>(&self, writer: &mut W) -> io::Result<()> {
    self.kind.write(writer)?;
    let presumed_origin: Option<Vec<u8>> = self.presumed_origin.clone().map(Into::into);
    writer.write_all(&presumed_origin.encode())?;
    self.output.write(writer)?;
    writer.write_all(&u16::try_from(self.data.len()).unwrap().to_le_bytes())?;
    writer.write_all(&self.data)
  }
```

**File:** processor/src/networks/bitcoin.rs (L474-524)
```rust
  fn segwit_data_pattern(script: &ScriptBuf) -> Option<bool> {
    let mut ins = script.instructions();

    // first item should be SHA256 code
    if ins.next()?.ok()?.opcode()? != OP_SHA256 {
      return Some(false);
    }

    // next should be a data push
    ins.next()?.ok()?.push_bytes()?;

    // next should be a equality check
    if ins.next()?.ok()?.opcode()? != OP_EQUALVERIFY {
      return Some(false);
    }

    Some(true)
  }

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
    }

    data.truncate(MAX_DATA_LEN.try_into().unwrap());
    data
  }
```

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
