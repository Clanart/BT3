### Title
Unprivileged sender can poison Serai's Bitcoin scanner classification by paying to publicly-derivable internal offset addresses - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The EL finding is an externally-controlled state change (a strategy whitelister flipping `thirdPartyTransfersForbidden`) that the protocol has no guard against, breaking a subsystem end-to-end. The Serai analog lives in the Bitcoin `Scanner`: it identifies received outputs by `script_pubkey` alone, and every script it watches corresponds to a deterministic, publicly-computable scalar offset of the group key. Any unprivileged party can therefore craft a Bitcoin transaction paying to Serai's internal Branch/Change/Forward scripts, and the scanner will emit it as a `ReceivedOutput` indistinguishable from a protocol-internal output — an externally injected state change Serai cannot reject.

### Finding Description
`Scanner::scan_transaction` accepts any transaction output whose `script_pubkey` is present in `self.scripts` and reports it as a `ReceivedOutput` carrying only the offset, the `TxOut`, and the outpoint — no authentication of *who* created it or *why*. [1](#0-0) 

The watched scripts are `p2tr_script_buf(key + G*offset)` for the zero offset plus any registered offset. [2](#0-1) 

The processor's fixed offsets are deterministic: `Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"branch"|"change"|"forward")`, so given the public group key, anyone can recompute the exact internal Branch, Change, and Forwarded P2TR addresses. [3](#0-2) 

Because the scanner keys outputs only by `script_pubkey`, a payment crafted by a third party to e.g. the Change or Forwarded script is classified by `kinds[offset_repr]` as `OutputType::Change` / `OutputType::Forwarded`, not `OutputType::External`. Downstream, `presumed_origin` and Serai `data` are only attached to `External` outputs, so the injected output silently enters the internal-output handling path with no provenance. [4](#0-3) 

Like the EL `thirdPartyTransfersForbidden` flag, this is not an extreme edge case: sending a standard P2TR payment is a normal, permissionless Bitcoin operation, and Serai has no check that an output on an internal script was actually produced by the protocol itself.

### Impact Explanation
- Attacker-crafted outputs land on `Change`/`Forwarded`/`Branch` kinds and are scheduled/spent through internal paths (forwarding, aggregation) the scheduler reserves for protocol-produced outputs, causing the multisig to sign spends of outputs under false assumptions about their role — concrete deviation between funds-received accounting and what the signer intended.
- An InInstruction embedded in a transaction paying only to an internal script is dropped (`data` only assigned for `External`), so a user who derives/pays an internal address has their Serai-bound data silently discarded while the funds are still absorbed as internal outputs.
- Repeated dust-sized-but-`>= N::DUST` deposits to the Forwarded/Change scripts force the scheduler to plan and sign aggregation/forwarding transactions consuming attacker-supplied inputs, burning protocol-held inputs' fee budget.

### Likelihood Explanation
Requires only a normal Bitcoin transaction to a deterministically derivable address (group key is public; offsets are `hash_to_F` of fixed strings). No collusion, no malformed encodings, no privileged position — the same reachability class as the EL strategy-whitelister toggle.

### Recommendation
Authenticate internal outputs rather than matching `script_pubkey` alone. Options:
- Keep a DB of outpoints the protocol itself created (change/forward outputs are always produced by known signed transactions) and only classify `Change`/`Forwarded`/`Branch` when the outpoint matches a protocol-created transaction; treat any other payment to those scripts as `External` (or flag for manual handling).
- Alternatively, commit the intended kind in a verifiable way (e.g., require internal outputs to be created by transactions spending a known protocol input) so third parties cannot mint "internal-kind" outputs.
- At minimum, document in `Scanner`/`register_offset` that script-only matching permits arbitrary parties to inject outputs of any registered kind.

### Proof of Concept
1. Observe the multisig's public group key `K` (even-Y tweaked Taproot key).
2. Compute `change_scalar = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"change")`, adjust by +1 steps until `K + G*offset` is even (mirroring `register_offset`), and build `addr = p2tr_script_buf(K + G*offset)`.
3. Broadcast a Bitcoin tx paying `>= DUST` sats to `addr`.
4. `scanner.scan_transaction` / `get_outputs` emits a `ReceivedOutput`/`Output` with `kind == OutputType::Change` and `presumed_origin == None` (since kind != `External`, the InInstruction/`data` pass is skipped).
5. The scheduler now holds an attacker-supplied "Change" output indistinguishable from genuine protocol change; it is consumed by future plans as an internal output, demonstrating externally injected state the system cannot distinguish or reject.

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

**File:** processor/src/networks/bitcoin.rs (L308-345)
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
