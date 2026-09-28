### Title
Attacker-controlled transaction outputs traverse into Serai's internal offset namespace (Branch/Change/Forwarded) via `Scanner::scan_transaction` script-pubkey-only matching - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The git-apply path-traversal class — crafted input escaping its intended boundary and landing in a namespace reserved for internal use — maps onto `bitcoin-serai`'s output scanner. `Scanner::scan_transaction` classifies received outputs solely by `script_pubkey` lookup into `self.scripts`, a map keyed only by script. The offsets that produce the internal Branch/Change/Forwarded scripts are derived from public, deterministic labels (`Secp256k1::hash_to_F(KEY_DST, b"branch"|b"change"|b"forward")` in `processor/src/networks/bitcoin.rs:333-344`), so any unprivileged party can compute those internal addresses and send an arbitrary Bitcoin transaction whose output lands inside the internal namespace. The scanner returns it as a `ReceivedOutput` carrying the internal offset — indistinguishable from an output the protocol itself created — analogous to a patch writing a file outside the working tree because the destination path was never constrained.

### Finding Description
`scan_transaction` accepts any on-chain transaction from the network and matches outputs purely on `output.script_pubkey` (`networks/bitcoin/src/wallet/mod.rs:205-211`). It records the matching `offset` into the returned `ReceivedOutput`. Callers then classify the output kind by that offset: `kinds[offset_repr]` yields `OutputType::External`, `Branch`, `Change`, or `Forwarded` (`processor/src/networks/bitcoin.rs:687-699`). The internal offsets are not secret — they are fixed scalars derived by `hash_to_F` over the constant DST `b"Serai Bitcoin Output Offset"` and public labels, initialized in `OnceLock`s (`processor/src/networks/bitcoin.rs:308-344`). There is no additional binding (no expected-txid, no plan/eventuality linkage, no amount check) between a scanned output and the internal role it is assigned. This mirrors CVE-2023-23946's flaw: the consumer trusts a caller-influenceable "path" (script/offset) to determine where the effect lands, without constraining it to the external-deposit "directory".

### Impact Explanation
An attacker broadcasting a transaction paying to the Branch, Change, or Forwarded script causes the processor to report the output with the corresponding internal `OutputType` rather than `External`. Concretely:

- A deposit sent to the Change address is treated as protocol-internal change. It is fed to the scheduler as a spendable input owned by the multisig while never being processed as a deposit (external deposits are expected to carry `InInstruction` data, which is only attached for `OutputType::External` at `processor/src/networks/bitcoin.rs:731-734`). This corrupts the accounting boundary between user deposits and protocol funds: unaccounted inputs enter the scheduler's UTXO pool and can be consumed by plans as if they were change from protocol transactions, mis-attributing value across plans.
- A payment sent to the Branch or Forwarded script is misclassified the same way, interfering with the scheduler's tracking of outputs that are supposed to correspond to specific plan branches.

The accepted impact is funds reported received under the wrong internal role: the scanner delivers outputs as if the protocol had created them, when in fact an outsider placed bytes into a protected namespace — the same integrity violation as overwriting a file outside the working tree.

### Likelihood Explanation
The attacker only needs to compute `hash_to_F(b"Serai Bitcoin Output Offset", <label>)`, add `G * offset` to the (public) multisig group key, iterate to even Y as `register_offset` does, and send a standard P2TR Bitcoin transaction — an entirely public, unprivileged action with no threshold or validator cooperation required. The deterministic offsets never rotate per-key label, so the internal addresses for every multisig are publicly computable. Exploitation reliability is high; impact is bounded to misaccounting/misclassification rather than direct key or signature forgery, so severity is Medium.

### Recommendation
Bind scanned outputs to their expected provenance: when an output matches an internal offset (Branch/Change/Forwarded), require it to appear in a transaction or context the processor itself initiated (e.g., correlate via the eventuality/plan tracking already present in `processor/src/multisigs/scanner.rs`), and treat otherwise-matching outputs as unrecognized or External. Alternatively, make internal offsets key- and context-dependent (include the multisig session/plan ID in the `hash_to_F` message) so external parties cannot precompute internal scripts.

### Proof of Concept
1. Observe the multisig group key `K` (public).
2. Compute `o = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"change")`; while `K + G*o` has odd Y, increment `o` (same loop as `register_offset` at `networks/bitcoin/src/wallet/mod.rs:184-195`).
3. Build a Bitcoin transaction paying ≥ `DUST` to `p2tr_script_buf(K + G*o)` and broadcast it.
4. The processor's `get_outputs` calls `scanner.scan_transaction(tx)`; the output matches `self.scripts`, returns `ReceivedOutput { offset: o, .. }`, and `kinds[o_repr]` yields `OutputType::Change` (`processor/src/networks/bitcoin.rs:693-699`). The attacker's output is now indistinguishable from protocol-internal change and is queued into the scheduler as protocol-owned spendable input without any `InInstruction` processing or deposit crediting. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L180-213)
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
```

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
