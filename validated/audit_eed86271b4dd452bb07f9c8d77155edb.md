### Title
`SignableTransaction::new` never rejects payments to the multisig's own watched scripts, letting a withdrawal be re-credited as a new deposit - (File: networks/bitcoin/src/wallet/send.rs)

### Summary
`SignableTransaction::new` accepts arbitrary `payments: &[(ScriptBuf, u64)]` without checking them against the wallet's own registered scripts (external, branch, change, forward). Because `Scanner::scan_transaction` matches purely on `script_pubkey` [1](#0-0) , a payment to the vault's own external address produces an on-chain output that `get_outputs` re-ingests as `OutputType::External` — a fresh deposit — even though those same funds were just paid out.

### Finding Description
The RFP bug is "an unregistered/unvalidated `recipientId` is accepted and later paid". The Serai analog: the "recipient" scripts in `payments` are never validated as not belonging to the wallet's own watched set.

- `SignableTransaction::new` copies each payment script verbatim into `tx_outs` with only dust/fee checks [2](#0-1) [3](#0-2) .
- The scanner registers the base `p2tr` script under `Scalar::ZERO` and fixed offsets derived from `hash_to_F(b"Serai Bitcoin Output Offset", ...)` for Branch/Change/Forwarded [4](#0-3) [5](#0-4) .
- `get_outputs` classifies any matching output by offset; the zero offset maps to `OutputType::External`, gets the tx's Serai `data` attached, and gets a `presumed_origin` [6](#0-5) .

A withdrawal whose destination `ScriptBuf` equals `p2tr_script_buf(group_key)` (the vault's public external address — derivable by anyone from the public group key) is signed, broadcast, and on the next block scan is emitted as an `External` received output: the protocol debits the withdrawal AND credits a new deposit of the same coins.

### Impact Explanation
Double-counting of vault funds: every sat paid to the vault's own external script is simultaneously disbursed and re-credited as a deposit (with attacker-controlled `data` if an `InInstruction` is present, and a self-derived `presumed_origin`). Repeated withdrawals-to-self let an unprivileged user mint unbounded deposit credit against the pool. The output is technically spendable (offset ZERO), but the protocol ledger is corrupted — "funds reported received" that are actually funds already spent out.

### Likelihood Explanation
Fully reachable by an unprivileged party with only public inputs: the vault's external `script_pubkey` is observable on-chain, and requesting a withdrawal to it only requires setting the destination address on an ordinary withdrawal/deposit flow. No collusion, no key material, no validator misbehavior required — `SignableTransaction::new` performs no exclusion of self-scripts [7](#0-6) . Severity Medium/High depending on downstream crediting rules in the processor.

### Recommendation
In `SignableTransaction::new` (or at the call site in `processor/src/networks/bitcoin.rs`), reject any payment `ScriptBuf` that is one of the wallet's own watched scripts — i.e., anything matching `scanner(key)`'s `scripts` map (external, branch, change, forward). Analogously to the report's fix (`revert` if recipient isn't registered), add:

```rust
// reject payments destined to any script the Scanner would match
if scanner_scripts.contains(&payment.0) { Err(TransactionError::SelfPayment)?; }
```

Alternatively, have the Scanner/`get_outputs` ignore outputs created by transactions spending the vault's own inputs (self-churn), but rejecting at construction is simpler and also prevents accidental fee loss.

### Proof of Concept
```rust
// networks/bitcoin/tests/wallet.rs style
let (keys, key) = keys();
let mut scanner = Scanner::new(key).unwrap();
let output = send_and_get_output(&rpc, &scanner, key).await;

// The vault's own external (un-offset) address, computable by anyone
let self_addr = p2tr_script_buf(key).unwrap();

// Withdrawal "to self": SignableTransaction::new does not reject it
let tx = SignableTransaction::new(
    vec![output.clone()],
    &[(self_addr.clone(), output.value() - FEE_ESTIMATE)],
    None, None, FEE,
).unwrap(); // succeeds — no self-payment check

let tx = sign(&keys, &tx);
// On the next scan, this payment output is matched by script_pubkey and
// reported as a NEW OutputType::External deposit with offset ZERO
let outputs = scanner.scan_transaction(&tx);
assert_eq!(outputs[0].offset(), Scalar::ZERO); // classified as External deposit
```

Result: the same UTXO value is paid out and re-emitted as a deposit — a double credit reachable entirely with public data.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L162-165)
```rust
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
```

**File:** networks/bitcoin/src/wallet/mod.rs (L199-213)
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
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-191)
```rust
  pub fn new(
    mut inputs: Vec<ReceivedOutput>,
    payments: &[(ScriptBuf, u64)],
    change: Option<ScriptBuf>,
    data: Option<Vec<u8>>,
    fee_per_vbyte: u64,
  ) -> Result<SignableTransaction, TransactionError> {
    if inputs.is_empty() {
      Err(TransactionError::NoInputs)?;
    }

    if payments.is_empty() && change.is_none() && data.is_none() {
      Err(TransactionError::NoOutputs)?;
    }

    for (_, amount) in payments {
      if *amount < DUST {
        Err(TransactionError::DustPayment)?;
      }
    }

    if data.as_ref().map_or(0, Vec::len) > 80 {
      Err(TransactionError::TooMuchData)?;
    }

    let input_sat = inputs.iter().map(|input| input.output.value.to_sat()).sum::<u64>();
    let offsets = inputs.iter().map(|input| input.offset).collect();
    let tx_ins = inputs
      .iter()
      .map(|input| TxIn {
        previous_output: input.outpoint,
        script_sig: ScriptBuf::new(),
        sequence: Sequence::MAX,
        witness: Witness::new(),
      })
      .collect::<Vec<_>>();

    let payment_sat = payments.iter().map(|payment| payment.1).sum::<u64>();
    let mut tx_outs = payments
      .iter()
      .map(|payment| TxOut { value: Amount::from_sat(payment.1), script_pubkey: payment.0.clone() })
      .collect::<Vec<_>>();
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
