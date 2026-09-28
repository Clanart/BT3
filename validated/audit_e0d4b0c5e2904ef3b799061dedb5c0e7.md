I have enough context on the key candidates. Let me verify one thing — how eventuality completions are matched for the `Forwarded` output type, since the Scanner matches on `script_pubkey` alone and the forward address is deterministic.### Title
Unprivileged sender can forge internal `Branch`/`Change`/`Forwarded` outputs by paying the deterministic offset addresses - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` classifies an output solely by its `script_pubkey` matching a registered offset script. The branch, change, and forward offsets are deterministic public values (`hash_to_F("Serai Bitcoin Output Offset", "branch"|"change"|"forward")`) applied to a public group key, so any Bitcoin user can compute and pay these addresses. A payment crafted by an unprivileged party is then reported as `OutputType::Forwarded`/`Change`/`Branch` — an "internal" classification the design intends only the multisig's own scheduler to produce — letting an attacker fabricate a forwarding/change relationship (an unauthorized association between their arbitrary transaction and the multisig's internal output types), analogous to CVE-2023-3920's unauthorized creation of a relationship between existing objects.

### Finding Description
`Scanner` maps `script_pubkey -> offset` and nothing else:

`networks/bitcoin/src/wallet/mod.rs:199-214` — `scan_transaction` returns a `ReceivedOutput` for any `output.script_pubkey` present in `self.scripts`, with no distinction between outputs created by the multisig itself and outputs created by arbitrary third parties.

The reserved offsets are derived deterministically and registered into `kinds`:

`processor/src/networks/bitcoin.rs:308-346` — `scanner()` registers `BRANCH_OFFSET`, `CHANGE_OFFSET`, `FORWARD_OFFSET`, each = `Secp256k1::hash_to_F(KEY_DST, b"...")`, and builds `kinds: offset_repr -> OutputType`.

`processor/src/networks/bitcoin.rs:686-700` — `get_outputs` calls `scanner.scan_transaction(tx)` and assigns `kind = kinds[offset_repr]`, so a third-party payment to `p2tr_script_buf(group_key + G * FORWARD_OFFSET)` is emitted as an `Output` with `kind == OutputType::Forwarded` (or `Change`/`Branch`), identical in shape to a genuinely protocol-produced output. `presumed_origin` is populated from `tx.input[0]`'s spent output (`processor/src/networks/bitcoin.rs:707-728`), which the attacker fully controls, and `data` is only attached to `External` outputs — meaning the attacker can't mark it `External`, they can only forge the *internal* kinds.

The forward/change/branch addresses are computable by anyone: `branch_address`/`change_address`/`forward_address` (`processor/src/networks/bitcoin.rs:661-674`) are pure functions of the public group key and the fixed hashed offsets. There is a single `FORWARD_OFFSET` reused for all plans, so the forged output is indistinguishable from any genuine forwarded payment for any pending plan under that key.

### Impact Explanation
The scanner is the sole witness the processor uses to decide that an on-chain event occurred. A forged `Forwarded`-kind output causes the processor to treat an attacker-created payment as the completion of a forwarding step the multisig was supposed to perform itself (forwarding is used when a scheduled plan can't be paid directly and must be routed through an intermediate output). Concretely this can cause:

- A pending plan's forwarding eventuality to be considered satisfied by an output the protocol never produced — the intended recipient is never actually paid, while the processor records the plan as progressed (funds reported as moved/received when the intended transfer did not happen).
- `Branch`/`Change`-typed outputs fabricated by an outsider entering the scheduler's output set with internal semantics, polluting balance/planning state that assumes such outputs only arise from the protocol's own transactions.

The unauthorized "relationship" here — an arbitrary third-party transaction bound to the multisig's reserved internal output types — is created entirely contrary to the protocol's intent, mirroring the report's bug class (a privileged-scoped operation — creating a fork relationship — being performable by a party who should not be able to create it). Reachability requires only broadcasting a standard Bitcoin transaction; no validator status, collusion, or key material is needed.

### Likelihood Explanation
Any Bitcoin user can derive the P2TR script for `group_key + G * hash_to_F(KEY_DST, "forward")` (and branch/change) and send ≥ `DUST` (10,000 sat) to it in a normal transaction. There is no authentication, proof, or nonce distinguishing protocol-created outputs from attacker-created ones — `scan_transaction` (`networks/bitcoin/src/wallet/mod.rs:205-211`) matches only on `script_pubkey` equality. The cost is one dust output plus fees, and because a single `FORWARD_OFFSET` is reused for all eventualities of a key, the attack is not plan-specific: one payment can satisfy whichever forwarding eventuality is pending.

### Recommendation
Do not classify outputs by `script_pubkey` alone for internal kinds. Options:

- Bind `Forwarded`/`Change`/`Branch` outputs to protocol context: e.g., require an authenticated linkage (the eventuality should only be considered completed by an output created by a transaction the multisig itself signed, verified against signed plan/txid records rather than by address match alone), or use a per-plan forward offset derived from the plan ID so a payment can only be matched to the specific plan whose address was attacked — and even then treat unsolicited payments to reserved addresses as `External` (or ignore them) rather than internal kinds.
- At minimum, derive per-eventuality offsets (e.g., `hash_to_F(KEY_DST, plan_id)`) so an attacker cannot precompute which internal address will be watched for a given pending plan, and never let externally-received outputs carry `Change`/`Branch`/`Forwarded` kinds without evidence they originated from a multisig-signed transaction.

### Proof of Concept
1. Observe the multisig's group key `K` (public on-chain / via `external_address`).
2. Compute `f = Secp256k1::hash_to_F(b"Serai Bitcoin Output Offset", b"forward")`, incrementing `f` until `p2tr_script_buf(K + G*f)` returns `Some` — exactly mirroring `register_offset` (`networks/bitcoin/src/wallet/mod.rs:180-196`). The same works for `"branch"`/`"change"`.
3. Broadcast any transaction with an output `TxOut { value: >= 10_000 sat, script_pubkey: p2tr_script_buf(K + G*f) }`. Inputs can be any UTXO the attacker owns.
4. On the next scanned block, `Bitcoin::get_outputs` (`processor/src/networks/bitcoin.rs:686-700`) emits `Output { kind: OutputType::Forwarded, presumed_origin: <attacker's input address>, output: ReceivedOutput { offset: f, ... }, data: vec![] }`, indistinguishable from a genuine protocol-forwarded output. `scanner.rs` (`processor/src/multisigs/scanner.rs:562-567`) pushes it into the emitted `outputs` list since it exceeds `DUST`, and the eventuality-completion path (`get_eventuality_completions`, `processor/src/multisigs/scanner.rs:569-590`) is driven off these same per-key block scans — so the forged output is consumed by the same pipeline that consumes authentic forwarded outputs.

No private material is required at any step; `f`, `K`, and the script are all public or publicly derivable.