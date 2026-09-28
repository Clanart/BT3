### Title
Attacker-can-minted deposits misclassified as privileged internal output types (Change/Branch/Forwarded) - (File: processor/src/networks/bitcoin.rs)

### Summary
The xxl-job advisory concerns permissions not being preserved/verified on a sensitive operation. The analog in Serai is the Bitcoin scanner's output classification: `OutputType` (External / Branch / Change / Forwarded) is a privilege label attached to received funds, yet it is derived solely from which registered offset's `script_pubkey` a TX output pays to. Because the offsets for Branch/Change/Forwarded are publicly derivable (`hash_to_F` of a fixed public DST), any unprivileged Bitcoin user can send funds to a privileged-type address and have the scanner report the deposit under the internal-only classification.

### Finding Description
`scanner()` in `processor/src/networks/bitcoin.rs:314-347` constructs a `Scanner` and registers three deterministic offsets: `Branch`, `Change`, and `Forwarded`, each computed as `Secp256k1::hash_to_F(KEY_DST, b"...")` over the constant public string `KEY_DST = b"Serai Bitcoin Output Offset"` [1](#0-0) . These offsets — and hence the corresponding P2TR addresses `key + G*offset` — are computable by anyone who knows the group's public key; no secret is involved.

`Scanner::scan_transaction` matches outputs purely by `script_pubkey` lookup [2](#0-1) , and `get_outputs` then maps the matched offset's bytes through the `kinds` table to assign `OutputType` [3](#0-2) . Nothing distinguishes "output we created as our own change/branch" from "output an arbitrary third party sent to that same address". The downstream attribution logic only special-cases `OutputType::External` (attaching `presumed_origin` and Serai `data` from the transaction) [4](#0-3)  — Change/Branch/Forwarded outputs from strangers are recorded with `presumed_origin: None` and empty data, indistinguishable from legitimately self-produced internal outputs.

The permissions check that would prevent this is the documented requirement in `Scanner::register_offset` that "offsets registered must be securely generated" [5](#0-4)  — but the processor deliberately uses *deterministic, public* offsets for its internal address types, so the permission boundary the classification assumes (only the multisig could produce these outputs) does not exist on-chain.

### Impact Explanation
Outputs are reported to the processor as internal-type outputs (Change, Branch, Forwarded) that were never produced by the multisig. These carry different semantics than `External` deposits: they skip the `presumed_origin`/InInstruction data path and enter the UTXO set under a classification that assumes the protocol itself created them. This corrupts accounting of how much was actually received via the deposit path versus internal flows, and inserts attacker-chosen outputs into the privileged spend/rotation pipeline (e.g., an attacker-fabricated "Change" output above `N::DUST` is later eligible as a plan input), enabling confusion between externally attributable deposits and internal flows — the analog of an unprivileged actor gaining access to a permissioned operation.

### Likelihood Explanation
Any Bitcoin user can derive the Branch/Change/Forwarded addresses from the well-known group public key (the DST and labels `b"branch"`, `b"change"`, `b"forward"` are hardcoded constants) and broadcast a transaction paying them. No collusion, no validator access, and no protocol participation is required — just an on-chain transaction of value ≥ `DUST` (10,000 sat).

### Recommendation
Bind internal-type outputs to proof of internal creation: e.g., require a per-key secret-derived component for Change/Branch/Forwarded offsets (so external parties cannot construct the addresses), or track expected change/branch outputs by the txids the processor itself signed and only classify matching script_pubkeys as internal types when they appear in self-authored transactions; otherwise treat them as `External`.

### Proof of Concept
1. Observe the multisig's group key `K` (public on-chain / in the validator set).
2. Compute `off_change = hash_to_F(b"Serai Bitcoin Output Offset", b"change")` and increment per `register_offset`'s parity loop until `K + G*off_change` is even; build `p2tr_script_buf(K + G*off_change)`.
3. Send ≥ 10,000 sat to that script in any transaction.
4. `get_outputs` scans the block, `scan_transaction` matches `script_pubkey`, `kinds[offset_repr]` yields `OutputType::Change`, and the attacker-authored output is reported to the processor as an internal change output with no `presumed_origin` — a permissioned classification granted to an unprivileged party.

### Citations

**File:** processor/src/networks/bitcoin.rs (L308-344)
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
```

**File:** processor/src/networks/bitcoin.rs (L686-699)
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
```

**File:** processor/src/networks/bitcoin.rs (L702-736)
```rust
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

**File:** networks/bitcoin/src/wallet/mod.rs (L177-179)
```rust
  /// The offsets registered must be securely generated. Arbitrary offsets may introduce a script
  /// path into the output, allowing the output to be spent by satisfaction of an arbitrary script
  /// (not by the signature of the key).
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
