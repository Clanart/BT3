### Cross-transaction instruction injection into External outputs - (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::get_outputs` accumulates matching outputs across every transaction in a block, then applies the currently processed transaction’s extracted Serai data and presumed origin to the entire accumulated list. An attacker who places a transaction paying a Serai `External` output later in the same block can overwrite the instruction data and presumed origin of unrelated `External` outputs found earlier in that block. Those bytes are later decoded as a `Shorthand` and converted into an executable `InInstructionWithBalance`, so the attacker can bind their own instruction to another depositor’s funds. [1](#0-0) [2](#0-1) 

### Finding Description
`get_outputs` declares `let mut outputs = vec![]` before iterating over `block.txdata[1 ..]`. [3](#0-2) 

For every transaction, newly discovered outputs are appended to that block-wide list. [4](#0-3) 

After scanning each transaction, the function checks whether the whole accumulated `outputs` list is non-empty rather than whether the current transaction produced outputs. [5](#0-4) 

It then derives `presumed_origin` solely from `tx.input[0]`, extracts Serai data solely from the current transaction, and assigns both to every accumulated output. [6](#0-5) 

The vulnerable statements are:

```rust
let data = Self::extract_serai_data(tx);
for output in &mut outputs {
  if output.kind == OutputType::External {
    output.data.clone_from(&data);
  }
  output.presumed_origin.clone_from(&presumed_origin);
}
``` [7](#0-6) 

The extracted bytes are attacker-controlled public transaction data: an `OP_RETURN` push is preferred, and otherwise the penultimate witness item is accepted when the revealed witness script has the expected `OP_SHA256 <hash> OP_EQUALVERIFY` prefix. [8](#0-7) 

Downstream, `instruction_from_output` accepts data on every `External` output, SCALE-decodes it as `Shorthand`, converts it into `RefundableInInstruction`, attaches the output’s full balance less aggregation costs, and returns either the explicit instruction origin or the presumed origin. [9](#0-8) 

Thus, the later attacker transaction does not merely add its own instruction: it replaces the instruction/origin metadata of unrelated earlier outputs in the same block.

### Impact Explanation
This is an unauthenticated transaction-data injection. An attacker can cause an earlier `External` deposit to be processed with attacker-selected `Shorthand` bytes and an attacker-controlled presumed refund origin. [7](#0-6) 

If the injected bytes decode as a valid `RefundableInInstruction`, Serai treats the victim output’s balance as funding that attacker instruction. [10](#0-9) 

For an instruction such as a transfer crediting the attacker, this can redirect the economic effect of the victim’s deposited funds to the attacker. Alternatively, malformed data plus the forged presumed origin can misroute a refund decision. The impact is cross-user theft or denial of deposit processing, not merely incorrect metadata display.

### Likelihood Explanation
The attacker only needs to broadcast a Bitcoin transaction that:

1. pays at least the dust threshold to one of the Serai-scanned scripts, and
2. appears in the same block after the target deposit transaction. [11](#0-10) 

The attacker cannot unilaterally choose the ordering used by miners, but Bitcoin transaction ordering permits this condition and an attacker can monitor the mempool, submit their transaction while the target is pending, and repeat attempts across blocks. No validator key, RPC access, threshold collusion, malformed curve encoding, or internal message is required. [12](#0-11) 

A simple `OP_RETURN` output carrying attacker-selected SCALE-encoded `Shorthand` bytes is sufficient to provide the injected command data. [13](#0-12) 

### Recommendation
Maintain a per-transaction output list inside `get_outputs`. Only annotate outputs created by the current transaction, then extend the block-level result after annotation.

Conceptually:

```rust
let mut outputs = vec![];
for tx in &block.txdata[1 ..] {
  let mut tx_outputs = vec![];

  for output in scanner.scan_transaction(tx) {
    let offset_repr = output.offset().to_repr();
    let kind = kinds[offset_repr.as_ref()];
    tx_outputs.push(Output {
      kind,
      presumed_origin: None,
      output,
      data: vec![],
    });
  }

  if tx_outputs.is_empty() {
    continue;
  }

  let presumed_origin = /* derive from tx.input[0] */;
  let data = Self::extract_serai_data(tx);

  for output in &mut tx_outputs {
    if output.kind == OutputType::External {
      output.data.clone_from(&data);
    }
    output.presumed_origin.clone_from(&presumed_origin);
  }

  outputs.extend(tx_outputs);
}
```

A regression test should place two independent transactions in one block: the first pays an `External` address with instruction A, and the second pays an `External` address with instruction B. The first output must retain instruction A and the second must retain instruction B. The test should also verify that an earlier transaction without Serai data is not assigned data by a later matching transaction. [14](#0-13) 

### Proof of Concept
Let `K` be an active scanned Bitcoin multisig key and `external(K)` its zero-offset Taproot address. `scanner(key)` associates the zero scalar with `OutputType::External`. [15](#0-14) 

Construct block `B` containing, in order:

1. `tx_victim`, paying `100_000` sats to `external(K)` and carrying `Shorthand` bytes crediting `victim_serai_account`.
2. `tx_attacker`, paying `10_000` sats to `external(K)` and carrying `Shorthand` bytes crediting `attacker_serai_account`.

When `get_outputs(B, K)` scans `tx_victim`, it appends the victim output to `outputs`. When it scans `tx_attacker`, it appends the attacker output. Because `outputs` is non-empty, it calls `extract_serai_data(tx_attacker)` and writes attacker bytes to every accumulated `External` output, including the victim output. [16](#0-15) 

The resulting objects are emitted as scanned outputs after passing the dust check. [11](#0-10) 

Subsequent processing calls `instruction_from_output` on the victim output. Its `data()` is now the attacker’s `Shorthand`, while its balance remains `100_000` sats minus the aggregation adjustment. The decoded instruction therefore executes with the victim’s deposited value rather than with the attacker output’s `10_000`-satoshi value. [9](#0-8)

### Citations

**File:** processor/src/networks/bitcoin.rs (L314-346)
```rust
fn scanner(
  key: ProjectivePoint,
) -> (Scanner, HashMap<OutputType, Scalar>, HashMap<Vec<u8>, OutputType>) {
  let mut scanner = Scanner::new(key).unwrap();
  let mut offsets = HashMap::from([(OutputType::External, Scalar::ZERO)]);

  let zero = Scalar::ZERO.to_repr();
  let zero_ref: &[u8] = zero.as_ref();
  let mut kinds = HashMap::from([(zero_ref.to_vec(), OutputType::External)]);

  let mut register = |kind, offset| {
    let offset = scanner.register_offset(offset).expect("offset collision");
    offsets.insert(kind, offset);

    let offset = offset.to_repr();
    let offset_ref: &[u8] = offset.as_ref();
    kinds.insert(offset_ref.to_vec(), kind);
  };

  register(
    OutputType::Branch,
    *BRANCH_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"branch")),
  );
  register(
    OutputType::Change,
    *CHANGE_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"change")),
  );
  register(
    OutputType::Forwarded,
    *FORWARD_OFFSET.get_or_init(|| Secp256k1::hash_to_F(KEY_DST, b"forward")),
  );

  (scanner, offsets, kinds)
```

**File:** processor/src/networks/bitcoin.rs (L473-523)
```rust
  // Expected script has to start with SHA256 PUSH MSG_HASH OP_EQUALVERIFY ..
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
```

**File:** processor/src/networks/bitcoin.rs (L686-739)
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
```

**File:** processor/src/multisigs/mod.rs (L39-93)
```rust
// InInstructionWithBalance from an external output
fn instruction_from_output<N: Network>(
  output: &N::Output,
) -> (Option<ExternalAddress>, Option<InInstructionWithBalance>) {
  assert_eq!(output.kind(), OutputType::External);

  let presumed_origin = output.presumed_origin().map(|address| {
    ExternalAddress::new(
      address
        .try_into()
        .map_err(|_| ())
        .expect("presumed origin couldn't be converted to a Vec<u8>"),
    )
    .expect("presumed origin exceeded address limits")
  });

  let mut data = output.data();
  let max_data_len = usize::try_from(MAX_DATA_LEN).unwrap();
  if data.len() > max_data_len {
    error!(
      "data in output {} exceeded MAX_DATA_LEN ({MAX_DATA_LEN}): {}. skipping",
      hex::encode(output.id()),
      data.len(),
    );
    return (presumed_origin, None);
  }

  let shorthand = match Shorthand::decode(&mut data) {
    Ok(shorthand) => shorthand,
    Err(e) => {
      info!("data in output {} wasn't valid shorthand: {e:?}", hex::encode(output.id()));
      return (presumed_origin, None);
    }
  };
  let instruction = match RefundableInInstruction::try_from(shorthand) {
    Ok(instruction) => instruction,
    Err(e) => {
      info!(
        "shorthand in output {} wasn't convertible to a RefundableInInstruction: {e:?}",
        hex::encode(output.id())
      );
      return (presumed_origin, None);
    }
  };

  let mut balance = output.balance();
  // Deduct twice the cost to aggregate to prevent economic attacks by malicious miners against
  // other users
  balance.amount.0 -= 2 * N::COST_TO_AGGREGATE;

  (
    instruction.origin.or(presumed_origin),
    Some(InInstructionWithBalance { instruction: instruction.instruction, balance }),
  )
}
```

**File:** processor/src/multisigs/scanner.rs (L559-566)
```rust
          let key_vec = key.to_bytes().as_ref().to_vec();

          // TODO: These lines are the ones which will cause a really long-lived lock acquisition
          for output in network.get_outputs(&block, key).await {
            assert_eq!(output.key(), key);
            if output.balance().amount.0 >= N::DUST {
              outputs.push(output);
            }
```

**File:** processor/src/tests/literal/mod.rs (L87-190)
```rust
      // make a transfer instruction & hash it for script.
      let serai_address = insecure_pair_from_name("alice").public();
      let message = Shorthand::transfer(None, serai_address.into()).encode();
      let mut data = Sha256::engine();
      data.input(&message);

      // make the output script => msg_script(OP_SHA256 PUSH MSG_HASH OP_EQUALVERIFY) + any_script
      let mut script = ScriptBuf::builder()
        .push_opcode(OP_SHA256)
        .push_slice(Sha256::from_engine(data).as_byte_array())
        .push_opcode(OP_EQUALVERIFY)
        .into_script();
      // append a regular spend script
      for i in main_addr.script_pubkey().instructions() {
        script.push_instruction(i.unwrap());
      }

      // Create the first transaction
      let tx = btc.get_block(new_block).await.unwrap().txdata.swap_remove(0);
      let mut tx = Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: vec![TxIn {
          previous_output: OutPoint { txid: tx.compute_txid(), vout: 0 },
          script_sig: Script::new().into(),
          sequence: Sequence(u32::MAX),
          witness: Witness::default(),
        }],
        output: vec![TxOut {
          value: tx.output[0].value - BAmount::from_sat(10000),
          script_pubkey: ScriptBuf::new_p2wsh(&script.wscript_hash()),
        }],
      };
      tx.input[0].script_sig = Bitcoin::sign_btc_input_for_p2pkh(&tx, 0, &private_key);
      let initial_output_value = tx.output[0].value;

      // send it
      btc.rpc.send_raw_transaction(&tx).await.unwrap();

      // Chain a transaction spending it with the InInstruction embedded in the input
      let mut tx = Transaction {
        version: Version(2),
        lock_time: LockTime::ZERO,
        input: vec![TxIn {
          previous_output: OutPoint { txid: tx.compute_txid(), vout: 0 },
          script_sig: Script::new().into(),
          sequence: Sequence(u32::MAX),
          witness: Witness::new(),
        }],
        output: vec![TxOut {
          value: tx.output[0].value - BAmount::from_sat(10000),
          script_pubkey: serai_btc_address.into(),
        }],
      };

      // add the witness script
      // This is the standard script with an extra argument of the InInstruction
      let mut sig = SECP256K1
        .sign_ecdsa_low_r(
          &Message::from_digest_slice(
            SighashCache::new(&tx)
              .p2wsh_signature_hash(0, &script, initial_output_value, EcdsaSighashType::All)
              .unwrap()
              .to_raw_hash()
              .as_ref(),
          )
          .unwrap(),
          &private_key.inner,
        )
        .serialize_der()
        .to_vec();
      sig.push(1);
      tx.input[0].witness.push(sig);
      tx.input[0].witness.push(public_key.inner.serialize());
      tx.input[0].witness.push(message.clone());
      tx.input[0].witness.push(script);

      // Send it immediately, as Bitcoin allows mempool chaining
      btc.rpc.send_raw_transaction(&tx).await.unwrap();

      // Mine enough confirmations
      let block_number = btc.get_latest_block_number().await.unwrap() + 1;
      for _ in 0 .. <Bitcoin as Network>::CONFIRMATIONS {
        btc.mine_block().await;
      }
      let tx_block = btc.get_block(block_number).await.unwrap();

      // verify that scanner picked up the output
      let outputs =
        match timeout(Duration::from_secs(30), scanner.events.recv()).await.unwrap().unwrap() {
          ScannerEvent::Block { is_retirement_block, block, outputs } => {
            scanner.multisig_completed.send(false).unwrap();
            assert!(!is_retirement_block);
            assert_eq!(block, tx_block.id());
            assert_eq!(outputs.len(), 1);
            assert_eq!(outputs[0].kind(), OutputType::External);
            outputs
          }
          _ => panic!("unexpectedly got eventuality completion"),
        };

      // verify that the amount and message are correct
      assert_eq!(outputs[0].balance().amount.0, tx.output[0].value.to_sat());
      assert_eq!(outputs[0].data(), message);
```
