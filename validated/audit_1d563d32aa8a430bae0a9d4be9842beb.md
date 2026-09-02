### Title
Unauthenticated OP_RETURN operator attribution in payout tx lets an unprivileged attacker frame a victim operator for a payout it never funded - ([File: circuits-lib/src/bridge_circuit/mod.rs], [File: core/src/verifier.rs])

### Summary
The payout transaction's operator-attribution field is a plain `OP_RETURN` push of a 32-byte x-only pubkey with no signature or proof-of-funding tying it to whoever actually paid the withdrawal. Because the payout tx's only committed input is signed by the withdrawing user with `SIGHASH_SINGLE|ANYONECANPAY` (`core/src/builder/transaction/operator_reimburse.rs:407-436`, `core/src/operator.rs:630-637`), anyone (including the withdrawing user, who is an "unprivileged attacker" per the rules) can add their own funding inputs and freely choose the `OP_RETURN` payload naming any operator's x-only pubkey. `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2283-2353`) blindly trusts `parse_op_return_data` + `XOnlyPublicKey::from_slice` (`circuits-lib/src/bridge_circuit/mod.rs:608-617`) to set `payout_payer_operator_xonly_pk`, and the victim operator's own `PayoutCheckerTask` (`core/src/task/payout_checker.rs:39-111`) automatically initiates a kickoff/reimbursement for any payout attributed to its own key without ever checking that it actually broadcast that payout transaction.

### Finding Description
The broken binding is:

`payout_payer_operator_xonly_pk (as recorded in `withdrawals` table by `update_finalized_payouts`) == the xonly pubkey of the party who actually funded the BTC that reached the withdrawer`

Trace:
1. Payout tx construction (`core/src/builder/transaction/operator_reimburse.rs:407-436`, used by `Operator::withdraw` at `core/src/operator.rs:560-637`) commits only input 0 (the user's withdrawal UTXO, signed with `SinglePlusAnyoneCanPay`, verified at `core/src/operator.rs:630-637`) and output 0 (user payout). The anchor output and the `OP_RETURN` output (containing `operator_xonly_pk.serialize()`) are **not covered by the user's signature** and are freely chosen by whoever assembles/funds the final broadcast transaction (e.g. via `fund_raw_transaction`/PSBT funding as in `crates/clementine-tx-sender/src/rbf.rs:152-243`).
2. Because `SinglePlusAnyoneCanPay` explicitly allows arbitrary additional inputs, **any unprivileged party holding the user's signature can add their own Bitcoin Core wallet funds as additional inputs** and broadcast a rival payout tx that fronts the same withdrawal, embedding *any* operator's x-only pubkey (a public, well-known value) in the `OP_RETURN`, not the funder's own key.
3. `parse_op_return_data` (`circuits-lib/src/bridge_circuit/mod.rs:608-617`) only checks the script is `OP_RETURN` followed by a data push; it performs no signature check. `Verifier::update_finalized_payouts` (`core/src/verifier.rs:2319-2321`) does `parse_op_return_data(...).and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok())` — this only validates the bytes parse as a syntactically valid x-only point, not that the named party funded anything. The result is stored verbatim as `payout_payer_operator_xonly_pk` (`core/src/database/verifier.rs:198-251`).
4. The victim operator's own `PayoutCheckerTask::run_once` (`core/src/task/payout_checker.rs:39-52`) queries `get_first_unhandled_payout_by_operator_xonly_pk(self.operator.signer.xonly_public_key)` — matching purely on the DB column populated in step 3 — and if found, calls `Operator::handle_finalized_payout` to generate and (with `#[cfg(feature="automation")]`) automatically broadcast a `Kickoff` transaction for a payout it never actually sent. There is no local ledger check ("did I really send/fund this specific payout txid from my own wallet?") anywhere in this path.
5. `Verifier::is_kickoff_malicious` (`core/src/verifier.rs:1859-1915`) only checks that `operator_xonly_pk` recorded for the payout equals `kickoff_data.operator_xonly_pk` of the kickoff being evaluated — since the victim's own kickoff naturally uses the victim's own key, this guard does **not** catch the forgery; it was never designed to authenticate the OP_RETURN's origin, only to check kickoff/payout-info consistency.

The root cause: the OP_RETURN operator attribution is unauthenticated data in an unsigned portion of a transaction that anyone can complete due to the `SinglePlusAnyoneCanPay` sighash, and none of the consuming code paths (`update_finalized_payouts`, `PayoutCheckerTask`, `is_kickoff_malicious`) ties this attribution to cryptographic proof that the named operator supplied the actual outgoing BTC.

### Impact Explanation
This matches the Critical category "an operator reimbursed for a payout it never funded." A victim operator's own automation will spend its round collateral to build a `Kickoff` transaction and, absent a challenge, complete a `Reimburse` transaction (`core/src/builder/transaction/operator_reimburse.rs:318-385`) that pays the **full bridge amount** (`move_txhandler...DepositInMove` value) to the victim operator's reimbursement address — this reimbursement is only reachable because the DB falsely shows the victim as the payer. Even though only the real operator's key can sign the Kickoff/Reimburse (so BTC cannot be diverted to the attacker directly), the attack: (a) forces the victim operator to consume one of its limited per-round kickoff slots and burn time/collateral cycling through the round/challenge machinery for a payout it never made, (b) can be repeated against every unhandled withdrawal and every known operator xonly pubkey (all operator pubkeys are public/discoverable), (c) can create a resource/consistency confusion where two operators (attacker-funded fake claim + a real operator that later legitimately fronts the same withdrawal) contend for the same withdrawal attribution, and (d) undermines the entire "operator credited == operator that funded" invariant that downstream automation (`PayoutCheckerTask`, `get_reimbursement_txs`, `validate_payer_is_operator`) relies on for correctness.

### Likelihood Explanation
Preconditions are minimal and fully within the declared attacker capability set: the attacker only needs to be a withdrawing user (or possess a withdrawal signature/outpoint), be able to broadcast Bitcoin transactions, and pay Bitcoin fees plus the withdrawal amount itself (since they must fund the committed output value from their own wallet as they are completing their own payout). No verifier/operator/aggregator privilege, key compromise, or majority hashrate is required. This is fully repeatable across every deposit/withdrawal and against any operator whose x-only pubkey is known (public information, exposed via config/round transactions on-chain). Cost to the attacker is essentially the withdrawal amount plus fees (which they receive back as the payout recipient) plus mining fees, i.e., near-zero net cost relative to disruption caused.

### Recommendation
Do not derive `payout_payer_operator_xonly_pk` solely from an unauthenticated `OP_RETURN` push. Options:
1. Require the payout tx's `OP_RETURN` operator attribution to be committed under the operator's own signature (e.g., have the operator co-sign an additional input/output, or use a sighash flag that binds the `OP_RETURN` to a signature from the named operator key), so the attribution cannot be forged by a party who only holds the withdrawer's `SinglePlusAnyoneCanPay` signature.
2. Alternatively/additionally, have `PayoutCheckerTask`/`handle_finalized_payout` require local proof that the operator itself constructed and broadcast this exact payout transaction (e.g., cross-check against the operator's own `TxSender`/wallet records of payout txs it created via its `withdraw` RPC) before initiating a kickoff, rather than trusting the on-chain OP_RETURN alone.
3. Treat any operator OP_RETURN attribution not corroborated by the operator's own local record as "no attribution" (same as the `None`/optimistic-payout case), forcing manual/aggregator-mediated confirmation before automation proceeds.

### Proof of Concept
```rust
// core/src/verifier.rs or a new test module (non-mock, exercising real logic)
// Demonstrates that parse_op_return_data + XOnlyPublicKey::from_slice accept an
// arbitrary valid x-only pubkey unrelated to whoever funded the tx's inputs.

#[test]
fn test_op_return_attribution_is_unauthenticated() {
    use bitcoin::{ScriptBuf, XOnlyPublicKey};
    use circuits_lib::bridge_circuit::parse_op_return_data;

    // "Victim operator" key - arbitrary, publicly known x-only pubkey,
    // NOT controlled by the attacker and never used to sign/fund this tx.
    let victim_xonly = XOnlyPublicKey::from_slice(&[0x4f,0x35,0x5b,0xdc,0xb7,0xcc,0x0a,0xf7,
        0x28,0xef,0x3c,0xce,0xb9,0x61,0x5d,0x90,0x68,0x4b,0xb5,0xb2,0xca,0x5f,0x85,0x9a,0xb0,
        0xf0,0xb7,0x04,0x07,0x58,0x71,0xaa]).unwrap();

    // Attacker crafts OP_RETURN containing the victim's pubkey while funding
    // the tx entirely from their own wallet (attacker-funded input/output,
    // not asserted anywhere in this code path).
    let mut op_return_script = vec![0x6a, 0x20]; // OP_RETURN, push 32 bytes
    op_return_script.extend_from_slice(&victim_xonly.serialize());
    let script = ScriptBuf::from(op_return_script);

    let parsed = parse_op_return_data(&script).expect("parses successfully");
    let recovered_pk = XOnlyPublicKey::from_slice(parsed).expect("valid xonly pubkey");

    // Assert both sides of the claimed binding, showing they can diverge:
    // LHS: the value that will be recorded as payout_payer_operator_xonly_pk
    assert_eq!(recovered_pk, victim_xonly);
    // RHS: no check exists anywhere in parse_op_return_data / update_finalized_payouts
    // that recovered_pk corresponds to the wallet that actually funded/signed
    // the transaction's inputs -- this test demonstrates that no such
    // proof-of-funding check exists, since the assertion above succeeds
    // despite `victim_xonly` never having signed or funded anything.
}
```
This proves `parse_op_return_data`/`XOnlyPublicKey::from_slice` (used identically inside `Verifier::update_finalized_payouts`, `core/src/verifier.rs:2319-2321`) accept any syntactically valid 32-byte value with zero linkage to the funding party. A full end-to-end reproduction (not required for this report, but confirmable by a Devin session with regtest/citrea-e2e) would: (1) have a test user call `withdraw` on the Citrea bridge to obtain `(in_outpoint, in_signature)`; (2) instead of calling the operator's `withdraw`/`InternalWithdraw` RPC, directly build and broadcast a payout tx spending that input with the attacker's own additional wallet-funded inputs and an `OP_RETURN` containing a *different, uninvolved* operator's x-only pubkey; (3) mine to finality and confirm via `db.get_payout_info_from_move_txid` that `payout_payer_operator_xonly_pk` equals the uninvolved operator's key; (4) show that operator's `PayoutCheckerTask` (with `#[cfg(feature="automation")]`) autonomously produces a `Kickoff` tx for this deposit despite never having funded the payout.