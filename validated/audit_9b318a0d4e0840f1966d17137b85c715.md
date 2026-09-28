### Title
Deposits to Serai's internal offset scripts (Change/Branch/Forwarded) are misclassified as internally-created outputs, breaking deposit accounting - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
`Scanner::scan_transaction` attributes ownership of a received output purely by matching `script_pubkey` against the registered offset scripts. The Branch, Change, and Forwarded addresses are deterministically derived from the public group key (`key + G*offset` where the offsets are `hash_to_F("Serai Bitcoin Output Offset", b"branch"|"change"|"forward")`), so any unprivileged party can compute them and send BTC directly to an internal script. This mirrors the report's bug class: the ledger records a balance whose provenance/kind does not match what the accounting layer assumes, so tracked obligations and actual backing diverge — deposits credited as internal transfers are never minted, while protocol-created outputs are indistinguishable from attacker-forged ones.

### Finding Description
`Scanner` keeps a `scripts: HashMap<ScriptBuf, Scalar>` map and `scan_transaction` emits a `ReceivedOutput` for *every* output paying to a registered script, regardless of which key/type the offset represents or who created the transaction:

- `register_offset` inserts `key + G*offset -> script` mappings and returns the normalized offset (`networks/bitcoin/src/wallet/mod.rs:180-196`).
- `scan_transaction` matches on `output.script_pubkey` alone and reports the output (`networks/bitcoin/src/wallet/mod.rs:199-214`).

Downstream, the kind of an output is recovered solely from the scalar offset: in `Bitcoin::get_outputs` the scanned offset's repr is looked up in `kinds`, which contains `External -> Scalar::ZERO`, `Branch`, `Change`, and `Forwarded` (`processor/src/networks/bitcoin.rs:316-346, 686-699`). Only `kind == External` outputs get the deposit `data`/`InInstruction` attached (`processor/src/networks/bitcoin.rs:731-735`); `Branch`/`Change`/`Forwarded` outputs are assumed to be outputs Serai itself created (branch outputs carry a `presumed_origin`, change is recycled into the input pool, forwards are expected only from a prior multisig).

Because the internal offset scripts are publicly derivable — `branch_address`/`change_address`/`forward_address` (`processor/src/networks/bitcoin.rs:661-674`) and the fixed `hash_to_F` offsets — an attacker can craft a transaction paying to, e.g., the Change or Forwarded script. The scanner then reports a `ReceivedOutput` whose `kind` is `Change`/`Forwarded` even though no Serai transaction created it. This is the same accounting fault as the report: a balance is recorded under a classification that carries different obligations than a normal deposit.

Concretely:

1. **Uncredited deposits.** A deposit to the Change/Branch script is picked up by the scheduler as an internally-owned output rather than an external deposit: it never flows through the `External` deposit path that mints `sriBTC` to the depositor (`processor/src/networks/bitcoin.rs:731-735`). The coins enter the spendable input set while the depositor receives nothing — funds are reported received that map to no liability, the direct analog of "underlying reduced while shares owed stay constant".

2. **Forged internal state.** A spoofed `Forwarded` output during a rotation window is indistinguishable from a genuine forward of a prior-multisig `External` output, yet it has no original `External` output from which to infer the `InInstruction`/refund address (spec `Multisig Rotation.md` step 5-6). The scheduler/eventuality logic then operates on internally-classified outputs that do not correspond to any real prior state, corrupting the accounting the same way the vault's share ledger diverged from actual Alchemix shares.

### Impact Explanation
Serai's accounting assumes `kind != External` outputs are self-created. Violating that invariant with an attacker-crafted Bitcoin transaction either (a) permanently steals user deposits from the mint/credit ledger while the multisig still controls the coins (insolvency-equivalent: liabilities under-recorded relative to held funds, breaking the flat-fee solvency model described in `spec/processor/UTXO Management.md`), or (b) injects fake Branch/Forwarded outputs that drive the scheduler/rotation logic on state it believes it produced itself, risking failed spends or misdirected refunds. Both are reachable by any party able to broadcast a Bitcoin transaction.

### Likelihood Explanation
The internal addresses are fully deterministic: `key` is the published multisig group key and the offsets are `hash_to_F("Serai Bitcoin Output Offset", <fixed tag>)` computed at `processor/src/networks/bitcoin.rs:308-344` and normalized by `register_offset`. No privileged access, collusion, or malformed cryptography is required — only a standard Bitcoin transaction to a computable P2TR address. Severity is Medium-to-High: deposits to the internal scripts are uncreditable by design (loss of user funds / silent donation to the multisig), and forged Forwarded outputs during rotation can corrupt scheduler state.

### Recommendation
Bind output classification to provenance, not just script. E.g., only treat an output as `Branch`/`Change`/`Forwarded` if its creating transaction is a known Serai eventuality/transaction (track created TXIDs and verify `output.outpoint().txid` against them), or use distinct scanning paths where internally-created outputs are registered at creation time rather than matched by script pattern. At minimum, document and enforce that externally-created transactions paying to internal offset scripts are either credited as `External` deposits or explicitly refunded.

### Proof of Concept
1. Observe the active multisig group key `K` (published in `Batch`s / `NetworkKeyDb`).
2. Compute `change_offset = normalize(hash_to_F(b"Serai Bitcoin Output Offset", b"change"))` per `register_offset` semantics (`networks/bitcoin/src/wallet/mod.rs:180-196`) and `change_script = p2tr(K + G*change_offset)` (`networks/bitcoin/src/wallet/mod.rs:80-86`).
3. Broadcast a Bitcoin transaction with a `TxOut { script_pubkey: change_script, value: >= DUST }`.
4. `get_outputs` scans the block, `scan_transaction` matches the script (`networks/bitcoin/src/wallet/mod.rs:205-212`), and `kinds[offset_repr]` yields `OutputType::Change` (`processor/src/networks/bitcoin.rs:693-697`). The output enters Serai's spendable input set with no `InInstruction`, no minted `sriBTC`, and no deposit record — a balance held by the vault with no corresponding obligation, the same underlying-vs-shares divergence as the report.