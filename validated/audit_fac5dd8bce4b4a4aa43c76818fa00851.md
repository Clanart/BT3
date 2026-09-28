### Title
Attacker-controlled deposits misclassified as internal Branch/Change/Forwarded outputs, bypassing deposit-data requirements - (File: processor/src/networks/bitcoin.rs)

### Summary
The Cover Protocol incident class is "unauthenticated creation of value": a public caller caused the contract to mint assets that bypassed the intended accounting path. In `serai`, the closest reachable analog lives in Bitcoin output scanning: `Bitcoin::get_outputs` classifies every scanned output by looking its scalar offset up in a fixed `kinds` map, but the offsets for `OutputType::Branch`, `OutputType::Change`, and `OutputType::Forwarded` are deterministic, publicly computable constants (`Secp256k1::hash_to_F(KEY_DST, b"branch"|b"change"|b"forward")`). Any unprivileged party can craft a P2TR output paying to one of these internal script_pubkeys and have the scanner report it as an internal output type rather than an `External` deposit.

### Finding Description
In `processor/src/networks/bitcoin.rs`, `scanner()` registers three internal offsets derived from fixed domain-separated hashes (`BRANCH_OFFSET`, `CHANGE_OFFSET`, `FORWARD_OFFSET`), all computed via `Secp256k1::hash_to_F(KEY_DST, ...)` on constant inputs. The effective offset is whatever `Scanner::register_offset` returns after evenness adjustment (`offset += 1` until `key + offset*G` is even), which is also fully deterministic and replayable by anyone (`networks/bitcoin/src/wallet/mod.rs:180-196`).

`get_outputs` then scans each transaction and tags every matched output with `kinds[offset_repr]` (`processor/src/networks/bitcoin.rs:687-699`). Crucially, only `OutputType::External` outputs receive the parsed Serai instruction data (`extract_serai_data`); outputs typed as `Branch`/`Change`/`Forwarded` carry no data and a `presumed_origin` derived from the first input (`lines 730-736`).

Because the internal offset scripts are publicly derivable, an attacker can send an arbitrary Bitcoin transaction output directly to, e.g., the `Change` or `Forwarded` P2TR script. `scan_transaction` matches only on `script_pubkey` (`wallet/mod.rs:205-211`), so the output is reported as an internal-type output. The protocol's accounting therefore records funds under a classification reserved for protocol-generated outputs, without the deposit-data binding required of `External` outputs.

### Impact Explanation
The scanner's output type drives downstream processor behavior: `External` outputs carry the depositor's `InInstruction` data, while `Change`/`Forwarded`/`Branch` outputs are treated as outputs the protocol itself produced. An attacker's unsolicited deposit to an internal script is reported as a protocol-internal output — real, spendable funds (the offset is known) recorded under a false provenance. Depending on downstream handling, this can corrupt internal bookkeeping (internal outputs credited/managed without any depositor instruction), inject attacker-chosen `presumed_origin` data into internal flows, or trigger forwarding logic on funds the protocol never moved. This maps to the Cover incident class: public inputs crossing an unguarded boundary into a privileged internal accounting state.

### Likelihood Explanation
The attack requires only sending a standard Bitcoin transaction to a computable address — no key knowledge, no validator cooperation, no timing. The offsets are constants derivable from `KEY_DST = b"Serai Bitcoin Output Offset"` and the literal suffixes, and the evenness adjustment loop is trivially replayed off-chain. The severity of the consequence depends on how the processor consumes non-`External` `Output`s, which is outside the in-scope crate, so the analog is rated Medium: the classification bypass is concrete and reachable, but the precise loss path depends on downstream consumers.

### Recommendation
Distinguish protocol-authorized internal outputs from adversarial payments to internal scripts. Options:
- Bind `Branch`/`Change`/`Forwarded` registration to a nonce or multisig-generation context so offsets are not derivable by external parties (e.g., incorporate the key-rotation/session transcript into the offset derivation rather than a fixed constant).
- When scanning, require internal-type outputs to appear in transactions the protocol itself constructed (check `tx.compute_txid()`/inputs against known spends) before accepting the internal classification.
- At minimum, treat any internal-type output arriving in a transaction that also pays an external user or carries no protocol-generated input as `External`, forcing the deposit-data path.

### Proof of Concept
1. Reconstruct the internal offsets off-chain:
   `change = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"change")`, then replicate `Scanner::register_offset`'s loop: while `p2tr_script_buf(key + G*offset)` is `None` (odd point), `offset += 1`. Same for `b"branch"` and `b"forward"`.
2. Compute `change_script = p2tr_script_buf(key + G*change_effective)`.
3. Broadcast any transaction paying dust-or-larger to `change_script`.
4. `Bitcoin::get_outputs` scans it via `scan_transaction`, finds `offset = change_effective`, and sets `kind = kinds[offset_repr] = OutputType::Change` — an attacker-created output classified as internal protocol change with no `InInstruction` data, versus the `External` path where `extract_serai_data` is applied.

Caveat: the exploitability of the misclassification beyond the scanner (whether it translates into incorrect crediting or fund handling) depends on processor code outside the in-scope crates, so the demonstrated primitive is the unauthorized type injection itself.