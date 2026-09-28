### Title
ReceivedOutput deserialization trusts an attacker-supplied spend offset unbound to the output's script_pubkey, misattributing received funds to a key that cannot spend them - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`ReceivedOutput::read` accepts a `Scalar` offset, a `TxOut`, and an `OutPoint` as three independent fields and returns them without verifying that `offset` actually corresponds to the Taproot script in `output.script_pubkey`. The scanner (`Scanner::scan_transaction`) only ever produces `(offset, script_pubkey)` pairs that are consistent by construction (`scripts: HashMap<ScriptBuf, Scalar>`), so the implicit invariant "offset is the registered offset for this script" is guaranteed on the scan path but not on the deserialization path. A `ReceivedOutput` built from untrusted bytes can therefore claim an arbitrary offset for a real on-chain output — the analog of the firewall bug where a policy sees `msg.sender`/`tx.origin` instead of the true (overridden) execution context: the *context* used to evaluate the output (which group key owns it) is taken from attacker bytes, not derived from the authoritative source.

### Finding Description
In `networks/bitcoin/src/wallet/mod.rs`:

- `ReceivedOutput` stores `{ offset: Scalar, output: TxOut, outpoint: OutPoint }` with no relation enforced between `offset` and `output.script_pubkey`.
- `ReceivedOutput::read` (lines 122–134) reads `offset = Secp256k1::read_F(r)` and consensus-decodes `output`/`outpoint`, returning the tuple as-is. `read_F` only checks canonical scalar encoding — any scalar is accepted.
- The scanner (`scan_transaction`, lines 199–214) only creates `ReceivedOutput`s where `scripts[output.script_pubkey] == offset`, i.e., `script_pubkey == p2tr_script_buf(key + offset·G)`.

Downstream, this invariant is assumed, not re-verified on the key-attribution path:

- `processor/src/networks/bitcoin.rs` `Output::key()` computes `key = read_G(xonly(script_pubkey)) - offset·G` (lines 112–122). With a corrupted offset, this returns a wrong group key, and `get_outputs`/the multisig scanner attributes the deposit to a key that does not control it (`assert_eq!(output.key(), key)` in the scheduler path then panics, or the output is scheduled under the wrong key).
- `SignableTransaction::multisig` (`networks/bitcoin/src/wallet/send.rs` lines 273–285) does check `p2tr_script_buf(keys.offset(offset).group_key()) == prevouts[i].script_pubkey`, but only by returning `None`, which `attempt_sign` converts into `expect("used the wrong keys")` — a panic. So an output accepted into the ledger via `read` cannot be spent: it is "received" but unspendable.

### Impact Explanation
An unprivileged party who can feed bytes to `ReceivedOutput::read` (an explicitly in-scope untrusted-read API) can take any real Serai-owned Taproot output — e.g., copy the `TxOut`/`OutPoint` of a legitimate deposit seen on-chain — and attach a different offset. The resulting `ReceivedOutput` reports funds as received under a group key that has no spend authority over them. Depending on the consumer, this either (a) attributes a deposit to the wrong key/session, causing the scheduler to build plans on unspendable inputs, or (b) hits `assert_eq!(output.key(), key)` / `expect("used the wrong keys")` panics. Either way it yields "funds reported received that are not spendable," satisfying the stated impact criteria. Severity: **Medium** (integrity/liveness of the accounting pipeline, not direct theft).

### Likelihood Explanation
Requires an attacker-controlled byte stream reaching `ReceivedOutput::read` (e.g., coordinator/exchange messages, DB records, or peer-provided output data). Within the audited call graph the scanner is the normal producer, but `read` is a public, untrusted-bytes API and the invariant it omits is exactly the class flagged by the report. Moderate likelihood where outputs are relayed rather than locally scanned.

### Recommendation
Bind the offset to the output at the trust boundary: either (a) make `ReceivedOutput::read`/`Output::read` verify `p2tr_script_buf(key + offset·G) == output.script_pubkey` for the registering key (requires passing the expected key/script map, as `Output::key` already assumes), or (b) store the key with the output and validate on read. At minimum, document that `ReceivedOutput::read` must only be fed scanner-produced bytes, and downgrade `multisig`/`attempt_sign` failures from panic to a recoverable error so a malformed input cannot abort the signer.

### Proof of Concept
```rust
// Context: a real deposit exists to scanner-registered offset o, producing
// TxOut with script_pubkey = p2tr(key + o·G).
// Attacker crafts bytes with a different offset o' != o:
let mut buf = vec![];
buf.extend((o + Scalar::ONE).to_bytes());      // wrong offset, canonical scalar
buf.extend(serialize(&real_txout));            // real TxOut from chain
buf.extend(serialize(&real_outpoint));         // real OutPoint

// ReceivedOutput::read succeeds — no binding check
let ro = ReceivedOutput::read(&mut buf.as_slice()).unwrap();

// Downstream attribution is wrong:
//   Output::key() = read_G(xonly(script)) - (o+1)·G  =  key - G  != key
// The output is reported as owned by `key - G`, and any SignableTransaction
// built from it fails `multisig()` (script mismatch -> None -> panic in
// attempt_sign), i.e. funds "received" but not spendable.
```