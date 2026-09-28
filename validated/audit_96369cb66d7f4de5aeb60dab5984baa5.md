### Title
Unprivileged senders can forge internal `Change`/`Forwarded`/`Branch` outputs because `Scanner` matches `script_pubkey` against publicly derivable offset addresses - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The CVE class is identifier-level confusion: an unprivileged party supplies an identifier (a `flow_id`) that indexes into state belonging to another user/role, reading or writing objects outside their authority. Serai's Bitcoin wallet reproduces this shape: `Scanner` attributes on-chain outputs to logical roles (`External` deposit vs. internal `Branch`/`Change`/`Forwarded`) purely by `script_pubkey` match. The offsets for the internal roles are deterministically derived with `hash_to_F` over a fixed, public DST — `Secp256k1::hash_to_F(KEY_DST, b"branch" | b"change" | b"forward")` in `scanner()` — so anyone can recompute the internal addresses. Any Bitcoin user can send a transaction paying to those scripts, causing the scanner to classify an outsider's output as protocol-internal state. [1](#0-0) [2](#0-1) 

### Finding Description
`Scanner::scan_transaction` looks up each `tx.output[i].script_pubkey` in `self.scripts` and returns a `ReceivedOutput` carrying the registered `offset` — with no binding to who created the output, which key produced the offset, or any authenticating tag. [3](#0-2)  `register_offset`'s docstring requires offsets be "securely generated" because they define the script-path semantics, yet `processor/src/networks/bitcoin.rs` derives `BRANCH_OFFSET`, `CHANGE_OFFSET`, and `FORWARD_OFFSET` via `Secp256k1::hash_to_F(KEY_DST, ...)`, which any third party can compute and thereby derive the internal addresses. [1](#0-0)  In `get_outputs`, the scanned offset is mapped back through `kinds` to an `OutputType`, and the attached Serai `data`/InInstruction is only populated for `OutputType::External` — deposits to internal scripts silently lose their instruction payload. [4](#0-3)  `OutputType::Change` is defined as "should be added to the available UTXO pool with no further action". [5](#0-4) 

### Impact Explanation
An unprivileged Bitcoin user — or a depositor who merely copies an observed change/forward address — sends funds to a script the scanner maps to `Change` or `Forwarded`. The output is ingested as protocol-internal state rather than an `External` deposit: no `data` is attached, no InInstruction is processed, and the depositor is never credited. The funds become spendable only by the threshold group as ordinary change, i.e., they are reported received but are not spendable/claimable by the party who sent them — a permanent crediting/attribution failure driven entirely by attacker-chosen transaction data (the `script_pubkey` acting as the cross-namespace identifier, exactly analogous to a foreign `flow_id`). For `Forwarded` classification the output additionally inherits forwarding semantics intended only for outputs the prior multisig itself produced. [6](#0-5) [5](#0-4) 

### Likelihood Explanation
Reachability is unconditional: the offsets are constants derived from a fixed DST (`b"Serai Bitcoin Output Offset"`), the addresses are computable by anyone, and sending a Bitcoin transaction requires no privileges. Exploitation requires only that a payment land on one of the three internal scripts — which can even happen accidentally if a user reuses a leaked/observed change address. There is no authentication step anywhere in `scan_transaction`/`get_outputs` to distinguish self-generated internal outputs from third-party ones. Likelihood of accidental occurrence is moderate; deliberate griefing (donating dust to pollute/dilute the internal UTXO pool and scheduler assumptions) is trivial. [7](#0-6) 

### Recommendation
Bind internal-role outputs to provenance, not just `script_pubkey`:
- In `Bitcoin::get_outputs`, only treat `Change`/`Branch`/`Forwarded` matches as internal when the spending transaction (or the TX creating them) was itself produced by the multisig — e.g., check the outpoint against a DB of TXIDs the protocol broadcast, and downgrade unmatched outputs to `External` handling (with `data` attached) or ignore them with a warning.
- Alternatively, derive internal offsets with a secret/HKD seed only the coordinator knows, so the internal scripts are not publicly computable — though this reduces auditability and still leaves replayable observed addresses; provenance checking is the robust fix.
- At minimum, document that funds sent to non-`External` addresses are irrecoverably absorbed as protocol change. [2](#0-1) 

### Proof of Concept
```rust
// Anyone can recompute the internal offsets and addresses:
let dst: &[u8] = b"Serai Bitcoin Output Offset";
let change_offset = Secp256k1::hash_to_F(dst, b"change");
// key is the multisig group key (public, observable on-chain)
let change_addr = p2tr_script_buf(group_key + (ProjectivePoint::GENERATOR * change_offset)).unwrap();

// Attacker/victim sends a normal Bitcoin payment to change_addr.
// In Bitcoin::get_outputs:
//   scanner.scan_transaction(tx) matches script_pubkey -> offset = change_offset
//   kinds[change_offset_repr] == OutputType::Change
//   `data`/InInstruction is NOT attached (only for OutputType::External)
// Result: output queued as internal change, added to the multisig UTXO pool,
// sender never credited; funds only spendable by the threshold group.
// Relevant code:
//   networks/bitcoin/src/wallet/mod.rs:199-214  (scan matches script_pubkey only)
//   processor/src/networks/bitcoin.rs:308-346   (public hash_to_F offsets)
//   processor/src/networks/bitcoin.rs:686-736   (kind lookup; data only for External)
```

Severity: Medium — permanent misattribution/loss of deposits reachable by any unprivileged Bitcoin sender, though it requires the sender to target a non-deposit address (by copying one or via griefing/dust donation).

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

**File:** networks/bitcoin/src/wallet/mod.rs (L180-214)
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

  /// Scan a transaction.
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

**File:** processor/src/networks/mod.rs (L77-82)
```rust
  // Should be added to the available UTXO pool with no further action
  Change,

  // Forwarded output from the prior multisig
  Forwarded,
}
```
