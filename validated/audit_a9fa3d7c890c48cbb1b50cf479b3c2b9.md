### Title
Attacker-controlled `script_pubkey` resolves to an internal offset (`Change`/`Branch`/`Forwarded`), causing deposits to be misclassified and uncredited - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The directory-traversal bug class — an attacker-supplied identifier escaping its expected namespace and resolving to an internal resource — maps onto Serai's Bitcoin scanner: `Scanner::scan_transaction` indexes a `HashMap<ScriptBuf, Scalar>` purely by the attacker-chosen `script_pubkey`, so any on-chain output paying to a script that collides with an internally-registered offset is returned as a `ReceivedOutput` carrying that internal offset and is then labeled with the internal `OutputType` (e.g. `Change`, `Branch`, `Forwarded`) instead of `External`.

### Finding Description
`Scanner` stores every registered output script in `self.scripts` keyed only by `ScriptBuf` [1](#0-0) . `scan_transaction` treats any transaction output whose `script_pubkey` exists in that map as a received output, with no distinction about who created it or why [2](#0-1) . The processor then maps the returned offset back to an `OutputType` via `kinds[offset_repr_ref]` and only attaches `data`/`presumed_origin` semantics for `OutputType::External` [3](#0-2) . The internal offset addresses — `branch_address`, `change_address`, `forward_address` — are deterministic functions of the public group key (`key + GENERATOR * offsets[&OutputType::*]`) [4](#0-3) , and the corresponding `script_pubkey`s are visible on-chain in every protocol transaction (the change output is appended directly to `tx_outs` [5](#0-4) ). A third party can therefore craft a transaction paying to e.g. the change-offset script. Just as `name=../` traverses out of the intended directory into an internal file, this `script_pubkey` traverses out of the "external deposit" namespace into an internal offset entry: the deposit is reported by `get_outputs` but tagged `OutputType::Change`/`Branch`/`Forwarded`, receives no `data` and a meaningless `presumed_origin`, and is consumed by internal change/forward handling rather than credited as a deposit.

### Impact Explanation
Funds sent by an unprivileged Bitcoin user to a colliding internal-offset address are reported received by `get_outputs` yet are not spendable/creditable as a normal deposit: they inherit an internal `OutputType`, get no instruction `data`, and are swept into internal aggregation/forwarding paths with an incorrect `presumed_origin` (the first input of the attacker's own transaction [6](#0-5) ). This satisfies the "funds reported received that are not spendable [as intended]" acceptance criterion — deposits are silently absorbed/misrouted by internal handling, corrupting the output ledger the coordinator acts on.

### Likelihood Explanation
Any external party can send a Bitcoin transaction (explicitly in scope as public input). Deriving a colliding script requires only the public group key and the deterministic offset derivation, or simply copying the change/branch `script_pubkey` observed in any published protocol transaction — no keys, threshold cooperation, or validator access needed. `register_offset`'s own documentation acknowledges that offset collisions change spend semantics ("arbitrary offsets may introduce a script path into the output" [7](#0-6) ), yet `scan_transaction` performs no provenance check beyond the script match. Medium likelihood: it requires the attacker to deliberately pay to an internal address, which is trivial once observed on-chain.

### Recommendation
Distinguish namespaces in the scanner instead of keying solely by `script_pubkey`: either (a) return the matched `OutputType`/registration tag from `scan_transaction` so callers can reject outputs matching internal offsets when processing external deposits, or (b) have `get_outputs` discard/flag outputs whose resolved kind is not `External` when the transaction was not produced by the protocol (e.g., verify the offset only for outputs the protocol itself created), preventing attacker-controlled scripts from traversing into internal offset entries.

### Proof of Concept
1. Observe any protocol Bitcoin transaction and extract the change output's `script_pubkey` (equivalently, compute `p2tr_script_buf(group_key + GENERATOR * offsets[&OutputType::Change])` from the public group key).
2. Broadcast a transaction paying `X` sats to that exact `script_pubkey` with no `OP_RETURN` data.
3. `Scanner::scan_transaction` matches `output.script_pubkey` in `self.scripts` and emits a `ReceivedOutput` whose `offset` is the internal change offset [8](#0-7) .
4. `get_outputs` looks up `kinds[offset_repr]` → `OutputType::Change`, so the `External` branch that attaches `data` is skipped and `presumed_origin` is set to the attacker's own spent input [9](#0-8) .
5. Result: the deposit is reported received but classified as internal change — never credited as an external deposit and later spent by internal aggregation as if protocol-owned, i.e., funds received yet not spendable/credited per intent.

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L153-166)
```rust
pub struct Scanner {
  key: ProjectivePoint,
  scripts: HashMap<ScriptBuf, Scalar>,
}

impl Scanner {
  /// Construct a Scanner for a key.
  ///
  /// Returns None if this key can't be scanned for.
  pub fn new(key: ProjectivePoint) -> Option<Scanner> {
    let mut scripts = HashMap::new();
    scripts.insert(p2tr_script_buf(key)?, Scalar::ZERO);
    Some(Scanner { key, scripts })
  }
```

**File:** networks/bitcoin/src/wallet/mod.rs (L177-179)
```rust
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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

**File:** processor/src/networks/bitcoin.rs (L661-674)
```rust
  fn branch_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Branch])))
  }

  fn change_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Change])))
  }

  fn forward_address(key: ProjectivePoint) -> Option<Address> {
    let (_, offsets, _) = scanner(key);
    Some(address_from_key(key + (ProjectivePoint::GENERATOR * offsets[&OutputType::Forwarded])))
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

**File:** networks/bitcoin/src/wallet/send.rs (L224-235)
```rust
    if let Some(change) = change {
      let (weight_with_change, vbytes_with_change) =
        Self::calculate_weight_vbytes(tx_ins.len(), payments, Some(&change));
      let fee_with_change = fee_per_vbyte * vbytes_with_change;
      if let Some(value) = input_sat.checked_sub(payment_sat + fee_with_change) {
        if value >= DUST {
          tx_outs.push(TxOut { value: Amount::from_sat(value), script_pubkey: change });
          weight = weight_with_change;
          needed_fee = fee_with_change;
        }
      }
    }
```
