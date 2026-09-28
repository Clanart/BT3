### Title
Outputs sent by third parties to internally-derived offset addresses are misclassified as `Branch`/`Change`/`Forwarded`, dropping their `InInstruction` data - ([File: networks/bitcoin/src/processor network glue / `processor/src/networks/bitcoin.rs`])

### Summary
`Bitcoin::get_outputs` reconstructs a `Scanner` containing the vault's base script plus three deterministic offsets (`BRANCH_OFFSET`, `CHANGE_OFFSET`, `FORWARD_OFFSET`, all `hash_to_F(KEY_DST, <literal>)`) and then classifies every scanned output purely by which offset produced the matching `script_pubkey`. Any output matching an offset script is tagged `OutputType::Branch`, `Change`, or `Forwarded`, and Serai-attached data is only propagated for `OutputType::External`. Since the offsets are public constants derived from the (public) group key, anyone can compute these internal addresses and send a transaction paying them while embedding a valid `InInstruction` in the same transaction's OP_RETURN. The deposit is reported as an internal output, its instruction is silently discarded, and downstream accounting treats it as vault-internal funds rather than a user deposit.

### Finding Description
The bug class of the reference report is a divergence between the protocol's internal accounting view and actual on-chain state, triggered when an outsider pushes assets into the vault through a path that bypasses the tracked entry point. The analog lives in `processor/src/networks/bitcoin.rs`:

- `scanner(key)` builds a `Scanner` whose `scripts` map contains the base P2TR script plus three fixed offsets (`hash_to_F(b"Serai Bitcoin Output Offset", b"branch"|"change"|"forward")`), and panics only on self-collision (`"offset collision"`). [1](#0-0) 
- `Scanner::scan_transaction` indexes outputs solely by `script_pubkey` and returns `ReceivedOutput`s carrying the registered offset. [2](#0-1) 
- `get_outputs` looks up `kinds[offset_repr]` to assign `OutputType`, then only attaches `extract_serai_data(tx)` (the OP_RETURN `InInstruction`) to outputs of kind `External`. [3](#0-2) 

The vault's forward/branch/change addresses are fully computable by any observer: the group key is public and each offset is a fixed `hash_to_F` of a public string. An attacker (or merely a mistaken user) who sends BTC to e.g. the `Forwarded` address while embedding `InInstruction::Transfer(their_serai_address)` produces a `ReceivedOutput` that is real and spendable by the vault, yet is labeled `OutputType::Forwarded`. Because of the `if output.kind == OutputType::External` gate, the instruction bytes are dropped, `presumed_origin` is still set (misleadingly), and the output enters the UTXO scheduler as ordinary internal funds.

This mirrors the EulerEarn bug exactly: the "real" state (a user deposit carrying a dispatch instruction) differs from the internally-tracked classification (a forwarder/change output), and the divergence causes the processing pipeline to operate on the wrong semantics — funds move but the intended operation is never executed.

### Impact Explanation
A user's deposit sent to a derived offset address is acknowledged on-chain and added to the vault UTXO set (`save_outputs` / `ScannerEvent::Block`), but its `InInstruction` is never dispatched: no `Transfer` mint, no `Dex` call. The coins become indistinguishable internal vault funds, so the depositor receives no credit and has no protocol-level path to reclaim them — a "funds reported received that are not spendable/credited as intended" outcome. Attackers can also deliberately pollute the scheduler's input set with dust-at-or-above-`N::DUST` outputs typed as `Change`/`Branch`, distorting aggregation decisions.

### Likelihood Explanation
Reachable by any unprivileged party: deriving the offset addresses requires only the public group key and the hard-coded `hash_to_F` labels. A single ordinary Bitcoin transaction to the forward/change/branch address triggers the misclassification on the next scanned block. No validator collusion, leaked keys, or malformed cryptography is needed — the scanner's script-only indexing does the work.

### Recommendation
Distinguish "the vault received funds" from "the vault generated this address internally". Concretely: when `get_outputs` scans an output whose kind is `Branch`/`Change`/`Forwarded` but which appears in a transaction the vault did not itself construct (no matching eventuality/plan, external `prevout` origin, or the presence of an `InInstruction` payload), treat it as `External` — or attach and process `extract_serai_data(tx)` for all scanned outputs regardless of kind, letting the in-instructions pallet decide validity. Alternatively, refuse to emit non-`External` classifications for transactions not originating from the scheduler's own signed plans.

### Proof of Concept
```rust
// Conceptual test against networks/bitcoin regtest harness (tests/wallet.rs pattern)

let (keys, key) = keys();                    // vault group key (public)
let rpc = rpc().await;
let scanner = Scanner::new(key).unwrap();

// Attacker derives the vault's *internal* forward address without any secret
let fwd_offset = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"forward");
let mut fwd = fwd_offset;
while p2tr_script_buf(key + (ProjectivePoint::GENERATOR * fwd)).is_none() {
  fwd += Scalar::ONE;                        // replicate register_offset parity bump
}
let fwd_script = p2tr_script_buf(key + (ProjectivePoint::GENERATOR * fwd)).unwrap();
let fwd_addr = Address::from_script(&fwd_script, Network::Regtest).unwrap();

// Attacker/user pays the forward address directly, with an InInstruction OP_RETURN
// tx: input(attacker) -> output[0]: fwd_script, output[1]: OP_RETURN <InInstruction>
let block = mine_tx(&rpc, attacker_tx).await;
let outputs = Bitcoin::get_outputs(&rpc, &block, key).await;

// BUG: output.kind == OutputType::Forwarded, NOT External
assert_eq!(outputs[0].kind(), OutputType::Forwarded);
// The InInstruction attached to the tx is dropped:
assert!(outputs[0].data().is_empty());
// Funds are now tracked as internal vault outputs; the deposit instruction
// (e.g. Transfer(serai_address)) is never emitted -> user loses credited funds.
```

The divergence is structural: `kinds[offset_repr]` at `processor/src/networks/bitcoin.rs:695` classifies by script alone, and the `output.kind == OutputType::External` gate at line 732 strips the instruction, so an externally-originated deposit is processed as an internal movement — the same "internal tracking vs real state" mismatch as `config[id].balance` vs `maxWithdraw` in the reference report.

### Citations

**File:** processor/src/networks/bitcoin.rs (L308-346)
```rust
const KEY_DST: &[u8] = b"Serai Bitcoin Output Offset";
static BRANCH_OFFSET: OnceLock<Scalar> = OnceLock::new();
static CHANGE_OFFSET: OnceLock<Scalar> = OnceLock::new();
static FORWARD_OFFSET: OnceLock<Scalar> = OnceLock::new();

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
