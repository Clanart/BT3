### Title
Attacker can spoof deposit classification by sending to a reserved offset script (change/branch/forward), causing received funds to be misreported - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` identifies relevant outputs by exact `script_pubkey` match and reports only the registered `offset`. Because offset scripts are deterministic — `p2tr_script_buf(key + G * offset)` where the well-known offsets are derived from `hash_to_F("Serai Bitcoin Output Offset", "branch"|"change"|"forward")` — any external sender can pay directly to a *reserved* offset script (e.g., the change script), causing their deposit to be reported as an internal/offset output rather than an external deposit. This is the analog of UI spoofing: the observed on-chain payment is presented with a spoofed semantic identity.

### Finding Description
`Scanner` maintains `scripts: HashMap<ScriptBuf, Scalar>` and `scan_transaction` returns a `ReceivedOutput` whose `offset` is whatever was registered for that `script_pubkey` — with no way to distinguish "external deposit to base key" from "attacker paying to a derived offset script" [1](#0-0) . `register_offset` only prevents script collisions between registered offsets; it does not authenticate that a given script is only produced by honest scheduling [2](#0-1) . Consumers classify outputs by offset alone: `Output.kind` is looked up via `kinds[offset_repr]` where External = `Scalar::ZERO` and Branch/Change/Forwarded are fixed hash-derived scalars [3](#0-2)  and [4](#0-3) . Additionally, `presumed_origin` is populated from `tx.input[0]`'s spent output, an attacker-chosen value shown as the deposit's origin [5](#0-4) .

### Impact Explanation
An unprivileged Bitcoin sender computes the public group key's change/branch/forward scripts and pays one directly. The scanner records the output with the corresponding non-zero offset, so downstream code reports it as `OutputType::Change`/`Branch`/`Forwarded` instead of `External`. An `External` deposit carries `data`/origin semantics; a misclassified deposit is credited to internal bookkeeping rather than to the depositor — funds are reported received with a spoofed type/origin, letting an attacker grief accounting or launder the provenance of a payment (the `presumed_origin` field displayed to users is whatever `tx.input[0]` happened to spend, fully attacker-controlled).

### Likelihood Explanation
Reachable by any party able to send a Bitcoin transaction to the multisig address: the group key is public, the offset scalars are fixed `hash_to_F` constants of public strings, and `p2tr_script_buf` is a pure function. No cooperation from any validator is needed. Cost is one on-chain payment of ≥ dust.

### Recommendation
Bind output classification to protocol intent rather than script shape alone — e.g., only classify `Branch`/`Change`/`Forwarded` outputs when they appear in transactions Serai itself constructed (matching the expected `txid`/eventuality), and treat unsolicited payments to non-zero offset scripts as `External` (or reject them loudly). Document `presumed_origin` as untrusted, attacker-supplied metadata.

### Proof of Concept
1. Read the public group key `K` for the active multisig.
2. Compute `offset = hash_to_F(b"Serai Bitcoin Output Offset", b"change")`, incrementing until `K + offset·G` is even (mirroring `register_offset` [6](#0-5) ).
3. Broadcast a transaction with `TxOut { script_pubkey: p2tr_script_buf(K + offset·G), value }`.
4. `scan_transaction` returns a `ReceivedOutput` with that reserved offset; `get_outputs` maps it via `kinds` to `OutputType::Change` [7](#0-6)  — the deposit is reported as internal change, not as an external deposit attributable to the sender.

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

**File:** processor/src/networks/bitcoin.rs (L313-346)
```rust
// Always construct the full scanner in order to ensure there's no collisions
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

**File:** processor/src/networks/bitcoin.rs (L707-729)
```rust
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
```
