### Title
Unauthenticated dust/zero-value deposits are reported as spendable multisig funds and force fee burn when spent - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
Analogous to the Lido report (anyone can push ETH into a vault and distort the accounting of a shared pool), any unprivileged party can send a Bitcoin transaction paying an arbitrarily small — including sub-dust or zero-value — output to a Serai multisig's P2TR `script_pubkey`. `Scanner::scan_transaction` matches solely on `script_pubkey` and records every matching output as a `ReceivedOutput` with no minimum-value or spendability check, and `Bitcoin::get_outputs` forwards all of them to the processor as received balance. When these outputs are later consumed by `SignableTransaction`, each input costs more in fees than a dust output is worth, so the wallet reports "funds received" that are economically unspendable and whose consolidation is a net loss of the pool's funds.

### Finding Description
`Scanner::scan_transaction` iterates all outputs of a transaction and pushes a `ReceivedOutput` whenever `self.scripts.get(&output.script_pubkey)` matches, with no check on `output.value` [1](#0-0) . The registered scripts cover the External (zero offset), Branch, Change, and Forwarded offsets derived via `hash_to_F` [2](#0-1) . `get_outputs` converts every scanned output into a protocol `Output` whose `balance()` reports the raw satoshi value [3](#0-2)  and [4](#0-3) .

On the spend side, `SignableTransaction::new` takes `Vec<ReceivedOutput>` as inputs unconditionally and only enforces `DUST` (546 sats) on *payments*, never on inputs [5](#0-4) . Each input adds a fixed ~57.5 vbytes of weight (the template `TxIn` in `calculate_weight_vbytes`), so the required fee grows with attacker-controlled input count [6](#0-5) . A zero-value or sub-dust P2TR output is consensus-valid inside a block (dust limits are mempool policy, not consensus), so a miner or any party getting such a tx mined can create outputs the scanner reports as balance but which can only be spent at a loss.

### Impact Explanation
- Reported multisig balance is inflated by outputs that cannot be profitably redeemed — "funds reported received that are not [economically] spendable".
- Every malicious input included in a spend burns `fee_per_vbyte * ~57.5` sats more than the input contributes, a pure fee drain on the shared pool funded by any external party at negligible cost.
- Mass dust deposits can push transactions toward `TooLargeTransaction`/`NotEnoughFunds`, obstructing legitimate withdrawals and forcing operators to choose between fee burn and stuck funds.

### Likelihood Explanation
The deposit address (`p2tr_script_buf` of the group key and registered offsets) is public and the attacker needs only to broadcast Bitcoin transactions — the exact reachable surface allowed by the rules. Cost is limited to the attacker's own transaction fees; no validator collusion, key knowledge, or RPC compromise is required.

### Recommendation
Apply a minimum-value / economic-spendability filter when admitting outputs: skip `output.value` below `DUST` (or below the fee cost of spending the input at the current fee rate) in `Scanner::scan_transaction` or in `get_outputs`, and consider capping/judiciously selecting inputs in `SignableTransaction::new` so sub-economical outputs are never consumed.

### Proof of Concept
1. Obtain the multisig's external P2TR `script_pubkey` (`Scanner::new(key)` registers it with offset `Scalar::ZERO`, `mod.rs:164`).
2. Craft and mine/relay a transaction with an output `TxOut { value: 1 sat (or 0), script_pubkey }` plus many dust outputs to the same script.
3. `scanner.scan_transaction(&tx)` returns `ReceivedOutput`s for all of them; `get_outputs` reports each as an `Output` credited to the multisig balance.
4. Any subsequent `SignableTransaction::new` including these inputs pays more fee than the dust is worth, or fails `NotEnoughFunds`/`TooLargeTransaction`, leaving value "received" that cannot be profitably spent.

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

**File:** processor/src/networks/bitcoin.rs (L128-130)
```rust
  fn balance(&self) -> ExternalBalance {
    ExternalBalance { coin: ExternalCoin::Bitcoin, amount: Amount(self.output.value()) }
  }
```

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

**File:** networks/bitcoin/src/wallet/send.rs (L68-83)
```rust
    let mut tx = Transaction {
      version: Version(2),
      lock_time: LockTime::ZERO,
      input: vec![
        TxIn {
          // This is a fixed size
          // See https://developer.bitcoin.org/reference/transactions.html#raw-transaction-format
          previous_output: OutPoint::default(),
          // This is empty for a Taproot spend
          script_sig: ScriptBuf::new(),
          // This is fixed size, yet we do use Sequence::MAX
          sequence: Sequence::MAX,
          // Our witnesses contains a single 64-byte signature
          witness: Witness::from_slice(&[vec![0; 64]])
        };
        inputs
```

**File:** networks/bitcoin/src/wallet/send.rs (L150-169)
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
```
