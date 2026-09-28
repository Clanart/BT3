### Title
Publicly derivable internal offsets let any sender forge "internal" output types in the Bitcoin scanner - ([File: processor/src/networks/bitcoin.rs](processor/src/networks/bitcoin.rs))

### Summary

The libgit2 NTFS ADS bug class is "two distinct names that are equivalent under the filesystem's resolution rules" — a security decision made on a name that an attacker can collide with. The Serai analog lives in `bitcoin_serai::wallet::Scanner` / the processor's `get_outputs`: the *type* of a received output (`External`, `Branch`, `Change`, `Forwarded`) is resolved purely by `script_pubkey` equivalence against offsets derived from public constants, so an unprivileged party can mint a UTXO that is byte-for-byte equivalent to a protocol-internal output and have it classified as such.

### Finding Description

`Scanner` indexes deposits solely by `script_pubkey` in `self.scripts` (`networks/bitcoin/src/wallet/mod.rs:205-211`). The processor constructs the scanner with four offsets: `Scalar::ZERO` (External) plus `Secp256k1::hash_to_F(KEY_DST, b"branch")`, `b"change"`, and `b"forward"`, where `KEY_DST = b"Serai Bitcoin Output Offset"` (`processor/src/networks/bitcoin.rs:308-344`).

These offsets are **completely public**: `hash_to_F` is a deterministic public function, the DST and the three labels are hard-coded constants, and the group key is public. Anyone can therefore compute `group_key + G*offset` for `Branch`, `Change`, and `Forwarded`, derive the corresponding P2TR `script_pubkey` via `p2tr_script_buf`, and pay to it.

In `get_outputs` (`processor/src/networks/bitcoin.rs:686-700`), the output `kind` is assigned by `kinds[offset_repr]` — so an attacker-crafted output to the Change script is indistinguishable from genuine protocol change. Crucially, `data` (the `InInstruction` crediting a depositor) is only attached when `output.kind == OutputType::External` (`processor/src/networks/bitcoin.rs:731-734`), and `presumed_origin` is set for all kinds. Two different semantic names — "attacker's deposit" and "protocol's own change/forward" — collapse to the same script and are resolved identically, exactly like a file and its ADS.

### Impact Explanation

- Funds sent to the Branch/Change/Forwarded scripts are credited to the protocol's internal output set rather than to any depositor: they receive no `InInstruction` data, so the sender is never credited while the multisig can still spend the coins. An attacker can trick a depositor into paying a "valid-looking" Serai address (e.g., by presenting the branch/forward address as a deposit address), causing permanent loss of credit for the depositor.
- Conversely, injected `Forwarded`/`Branch` outputs pollute the protocol's internal bookkeeping: outputs the protocol never created are treated as equivalent to outputs it did, since authentication is by script alone with no provenance check. This can corrupt the assumptions behind aggregation/forwarding logic that presumes such outputs are self-generated.

### Likelihood Explanation

Medium likelihood: exploitation requires a victim to pay to a derived internal script (or an attacker spending their own funds to forge internal outputs). The offsets require no secret material — only the public group key and public constants — so address derivation is trivial. This maps to a Medium severity analog: a reachable, unprivileged equivalence confusion causing incorrect classification of funds, not a direct key or signature forgery.

### Recommendation

- Bind provenance into output classification: only treat `Branch`/`Change`/`Forwarded` outputs as internal if they appear in transactions the protocol itself constructed (e.g., match on `txid` of known aggregation/forward transactions), rather than by `script_pubkey` equivalence alone.
- Alternatively, embed an internal-only marker (e.g., a commitment in the tweak or a script-path leaf known only to the protocol) so that externally constructible scripts are never byte-equivalent to internal ones.

### Proof of Concept

```rust
// Anyone can do this — no secrets required:
let dst: &[u8] = b"Serai Bitcoin Output Offset";
let branch_offset = Secp256k1::hash_to_F(dst, b"branch");          // public constant derivation
let mut off = branch_offset;
// replicate register_offset's parity bump
while p2tr_script_buf(group_key + (ProjectivePoint::GENERATOR * off)).is_none() {
  off += Scalar::ONE;
}
let branch_script = p2tr_script_buf(group_key + (ProjectivePoint::GENERATOR * off)).unwrap();
// Pay to `branch_script` on-chain. processor::networks::bitcoin::Bitcoin::get_outputs
// will classify it as OutputType::Branch (kinds[offset_repr]), attach no InInstruction
// data, and treat it as a protocol-internal output — indistinguishable from real change.
```

Key code: `Scanner::scan_transaction` matches on `output.script_pubkey` alone ( [1](#0-0) ), internal offsets are derived from public constants ( [2](#0-1) ), and non-`External` outputs never receive instruction data ( [3](#0-2) ).

### Citations

**File:** networks/bitcoin/src/wallet/mod.rs (L205-211)
```rust
      if let Some(offset) = self.scripts.get(&output.script_pubkey) {
        res.push(ReceivedOutput {
          offset: *offset,
          output: output.clone(),
          outpoint: OutPoint::new(tx.compute_txid(), vout),
        });
      }
```

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

**File:** processor/src/networks/bitcoin.rs (L730-735)
```rust
      let data = Self::extract_serai_data(tx);
      for output in &mut outputs {
        if output.kind == OutputType::External {
          output.data.clone_from(&data);
        }
        output.presumed_origin.clone_from(&presumed_origin);
```
