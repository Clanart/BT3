### Title
External senders can spoof internal output types via publicly derivable offset addresses, causing deposit instructions to be silently dropped - (File: processor/src/networks/bitcoin.rs)

### Summary
`Bitcoin::get_outputs` classifies every scanned output purely by looking up the matched script in the `kinds` map (`External`, `Branch`, `Change`, `Forwarded`). The Branch/Change/Forwarded addresses are deterministic public functions of the group key (offsets are `hash_to_F(KEY_DST, b"branch"|"change"|"forward")`), so any unprivileged third party can compute them and send Bitcoin to an internal-type address. The protocol implicitly assumes only Serai-generated transactions pay to these offsets (the "deny-list"), but that assumption is never enforced — the check is applied to the `script_pubkey` alone, while the actual transaction and its sender are attacker-controlled. This mirrors CVE-2022-43699, where a deny-list keyed on the domain string was bypassed because the attacker controlled the DNS resolution it derived from. [1](#0-0) [2](#0-1) [3](#0-2) 

### Finding Description
`scanner()` registers three well-known offsets — `Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"branch")`, `b"change"`, `b"forward"` — plus `Scalar::ZERO` for `External`, and builds `kinds` mapping each offset's repr to its `OutputType` [4](#0-3) . Since `hash_to_F` and the DST are public constants, every internal address (`branch_address`, `change_address`, `forward_address`) is publicly computable for any known group key.

`Scanner::scan_transaction` reports any output whose `script_pubkey` matches a registered script, regardless of who created the transaction [3](#0-2) . `get_outputs` then unconditionally assigns `kind = kinds[offset_repr]` and — critically — only attaches `extract_serai_data(tx)` (the embedded `InInstruction`) when `kind == OutputType::External` [5](#0-4) . `presumed_origin` is also taken verbatim from `tx.input[0]`'s spent output, fully attacker-controlled.

### Impact Explanation
An attacker (or phishing counterparty) who convinces a depositor to pay to a Branch/Change/Forwarded address — or front-runs by paying to one themselves — produces an output the processor treats as an internally generated output rather than an external deposit. The `InInstruction` data is dropped (only `External` outputs receive `data`), so the depositor's Serai-side instruction (e.g., a transfer destination) is never processed even though real BTC arrived at the multisig. This produces funds received that do not trigger their intended crediting/instruction path, and spoofed `OutputType::Branch`/`Change`/`Forwarded` outputs injected into the scheduler's view by an unprivileged sender. The analog to the CVE's "deny-list disregarded" is exact: the classification policy is applied to `script_pubkey` (the "domain name"), while the attacker controls the underlying origin data (the "DNS records"), and internal-kind addresses are reachable by anyone rather than only by the protocol.

### Likelihood Explanation
Reachable by any party that can broadcast a Bitcoin transaction — the exact public-input surface in scope (untrusted Bitcoin transactions). The offsets are constant per group key and trivially computed; no collusion, leaked key, or malicious validator is needed. Exploitation requires a depositor to pay to an internal address (social engineering or address substitution), or is exercised directly when an attacker deliberately injects fake internal-type outputs to pollute the output stream. Medium likelihood, bounded impact — the funds remain under multisig control, but the instruction is irreversibly unlinked from the deposit.

### Recommendation
Bind internal `OutputType`s to internally generated transactions rather than `script_pubkey` alone — e.g., only accept `Change`/`Forwarded`/`Branch` outputs when they appear on transactions Serai itself signed and broadcast (match by expected txid/outpoint from the scheduler's own plans), or reject/misclassify-flag outputs arriving at internal offsets from third-party-funded transactions. At minimum, attach `data`/`presumed_origin` regardless of kind so a misclassified deposit's instruction is not silently discarded.

### Proof of Concept
```rust
// Any party computes the internal addresses for a known group key:
let branch_off   = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"branch");
let branch_addr  = address_from_key(group_key + (ProjectivePoint::GENERATOR * branch_off));

// Attacker induces (or itself makes) a deposit to `branch_addr` while
// embedding an InInstruction (OP_SHA256 <hash> OP_EQUALVERIFY) in an input.
// In Bitcoin::get_outputs:
//   scanner.scan_transaction(tx) matches script_pubkey -> offset != ZERO
//   kind = kinds[offset_repr] == OutputType::Branch   (not External)
//   `if output.kind == OutputType::External` fails -> output.data stays empty
// Result: BTC is received at the multisig, reported as an internal Branch
// output, and the depositor's Serai instruction is silently dropped.
```
The scanner loop at `processor/src/networks/bitcoin.rs:686-700` and the `kind == OutputType::External` gate at lines 731-735 show there is no check that a Branch/Change/Forwarded output was produced by Serai's own spend, only that its `script_pubkey` matches.

Caveat: I did not fully trace the scheduler's downstream handling of each `OutputType` in `processor/src/multisigs/scheduler/utxo.rs`, so the precise degree to which a spoofed `Branch`/`Change` output corrupts plan construction (vs. only dropping the deposit instruction) is inferred from the classification gate shown above.

### Citations

**File:** processor/src/networks/bitcoin.rs (L308-347)
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
}
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
