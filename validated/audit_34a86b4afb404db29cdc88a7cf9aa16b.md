### Title
Withdrawal payout OP_RETURN operator attribution is unauthenticated, allowing front-running to credit reimbursement to an operator that never funded the payout - (File: core/src/verifier.rs)

### Summary
`Verifier::update_finalized_payouts` attributes a withdrawal's `payout_payer_operator_xonly_pk` purely from the OP_RETURN bytes of whichever Bitcoin transaction is first observed spending the withdrawal UTXO, without re-verifying that this transaction was authorized by, or paid for by, the named operator. Because the withdrawal signature uses `SinglePlusAnyoneCanPay` and only commits to the single payout output at the signed input's index, an unprivileged attacker who has the (public) withdrawal outpoint, script pubkey/amount and signature bytes can construct their own transaction that satisfies the signed output but appends an arbitrary OP_RETURN naming any operator, race it into a block, and permanently fix the DB attribution for that withdrawal index to an operator who never funded it.

### Finding Description
Binding claimed: `withdrawals.payout_payer_operator_xonly_pk` for index `i` == the xonly pubkey of the operator that actually fronted BTC equal to the withdrawal amount to the user for index `i`.

Code path:
- `get_payout_txs_for_withdrawal_utxos` (core/src/database/verifier.rs:170-196) joins `withdrawals.withdrawal_utxo_txid/vout` against `bitcoin_syncer_spent_utxos` and returns whichever `spending_txid` is recorded as having spent that exact outpoint — this is simply the first transaction confirmed on Bitcoin that spends it, with no notion of "authorized operator".
- `update_finalized_payouts` (core/src/verifier.rs:2283-2353) takes that transaction, extracts the first OP_RETURN output via `get_first_op_return_output`/`parse_op_return_data`, and unconditionally interprets its bytes as `operator_xonly_pk` [1](#0-0) . It then writes this value straight into `payout_payer_operator_xonly_pk` via `update_payout_txs_and_payer_operator_xonly_pk` [2](#0-1) . No schnorr/signature check is performed at this point to confirm the transaction's OP_RETURN was produced or authorized by the named operator.
- The withdrawal signature is enforced to use `TapSighashType::SinglePlusAnyoneCanPay` in `parse_withdrawal_sig_params` (core/src/rpc/parser/operator.rs:161-203). `SinglePlusAnyoneCanPay` binds only the spent input and the output sharing its index; any other outputs (such as an OP_RETURN placed at a different index) are excluded entirely from the sighash. The legitimate flow in `Operator::withdraw` verifies the schnorr signature against `payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)` [3](#0-2)  — again only covering the payout output at index 0, not the OP_RETURN. Since the withdrawal outpoint, script pubkey, amount and the raw signature bytes are all public per the threat model (they must be, since anyone must be able to check `get_withdrawal_utxo_from_citrea_withdrawal` state and craft the exact output the signature commits to), any unprivileged party can build an equivalent transaction spending the same UTXO to the same signed output while attaching a different, self-chosen OP_RETURN and broadcast/mine it ahead of the real operator's `Operator::withdraw` broadcast.
- Because a Bitcoin outpoint can only be spent once, whichever transaction confirms first permanently determines the `spending_txid` returned by `get_payout_txs_for_withdrawal_utxos`, and hence permanently fixes `payout_payer_operator_xonly_pk` for that index — the real operator's later attempt to broadcast a payout for the same withdrawal will simply fail as a double-spend.
- Downstream consumers trust this DB field as ground truth: `is_kickoff_malicious` (core/src/verifier.rs:1857-1915) only checks that the kickoff's operator matches the (possibly attacker-forged) `payout_payer_operator_xonly_pk`, and `get_first_unhandled_payout_by_operator_xonly_pk` / `PayoutCheckerTask::run_once` (core/src/task/payout_checker.rs:31-113) drive that named operator into `handle_finalized_payout` and the round/kickoff reimbursement flow, i.e., toward crediting collateral reimbursement to that operator.

Existing guards evaluated:
- `SECP.verify_schnorr` in `Operator::withdraw` (core/src/operator.rs:632-637) only protects the operator-initiated RPC flow; it never gates what actually gets attributed once a transaction is observed on-chain in `update_finalized_payouts`, so it does not stop this attack.
- `is_kickoff_malicious` only cross-checks consistency between the (already-poisoned) DB value and the kickoff sender; it cannot detect that the DB value itself was set by an unauthorized OP_RETURN.
- No DB uniqueness/ownership constraint exists that ties `payout_payer_operator_xonly_pk` to a signature authenticating the OP_RETURN content.

### Impact Explanation
An attacker can permanently and unilaterally fix which operator xonly pubkey is credited as having funded a given withdrawal index, without that operator ever spending money. This directly enables:
- An operator being pulled into the reimbursement/kickoff pipeline (`PayoutCheckerTask`, `handle_finalized_payout`, round/kickoff/reimburse transaction graph) for a payout it never made — matching the Critical category "an operator reimbursed for a payout it never funded."
- The withdrawal outpoint being permanently spent by the attacker's transaction, so the legitimate/intended operator can never front the same withdrawal — matching "an honest operator permanently unable to be reimbursed" for that index.
- Repeatable per withdrawal index; the attacker only needs to win the race for that one UTXO, at the cost of standard Bitcoin fees, with no special privilege beyond broadcasting a transaction. Blast radius spans every future withdrawal, as it targets a systemic gap in `update_finalized_payouts`, not a one-off bug.

### Likelihood Explanation
The preconditions match the stated unprivileged attacker capabilities exactly: knowledge of the withdrawal outpoint/script (both public via Citrea's withdrawal registration event) and possession of the raw signature bytes (necessarily obtainable/observable since the user or operator must transmit them to be spendable, and the sighash type explicitly leaves everything but the signed output unauthenticated). The attacker cost is limited to Bitcoin transaction fees and winning a mining race against the legitimate operator's broadcast — entirely feasible given operators do not have privileged mempool access. This is repeatable across every withdrawal.

### Recommendation
Do not derive `payout_payer_operator_xonly_pk` from an unauthenticated OP_RETURN. Bind the operator identity to the withdrawal cryptographically — e.g., require the OP_RETURN (or an equivalent commitment) to be covered by the sighash (use `All`/`AllPlusAnyoneCanPay` for the OP_RETURN output, or include a separate operator-signed authorization that verifiers check against a known operator set with `SECP.verify_schnorr` before writing `payout_payer_operator_xonly_pk`) so that only a transaction actually authorized by a registered operator can attribute the payout to that operator, and so that unauthenticated data smuggled in by any transaction that happens to spend the outpoint cannot poison the attribution table.

### Proof of Concept
```
cargo test update_finalized_payouts_op_return_unauthenticated_attribution
```
Plan:
1. Set up a deposit and register a withdrawal (index `i`) as in `update_get_payout_txs_from_citrea_withdrawal`/e2e helpers, obtaining the withdrawal UTXO outpoint, required output script_pubkey/amount, and the `SinglePlusAnyoneCanPay` signature (as produced by `generate_withdrawal_transaction_and_signature`).
2. Without calling `Operator::withdraw`, directly craft a transaction spending the withdrawal UTXO to the signed output at index 0, and attach a second output that is `OP_RETURN <arbitrary_real_operator_xonly_pk>` chosen by the "attacker" (not that operator's own action).
3. Broadcast and mine this transaction ahead of any operator-issued payout.
4. Run the verifier's block sync so `update_finalized_payouts` processes the block, then call `db.get_payout_info_from_move_txid`/`get_payer_xonly_pk_blockhash_and_kickoff_txid_from_deposit_id`.
5. Assert `payout_payer_operator_xonly_pk == arbitrary_real_operator_xonly_pk` even though that operator never called `Operator::withdraw` or signed/broadcast any transaction — i.e., assert the binding "`payout_payer_operator_xonly_pk` == operator that funded the withdrawal" is false: the DB attributes an operator that performed no `Operator::withdraw` call, while the real intended payer can never succeed (its later `withdraw` attempt fails since the withdrawal UTXO is already spent).

### Citations

**File:** core/src/verifier.rs (L2312-2321)
```rust
            // Find the first output that contains OP_RETURN
            let circuit_payout_tx = CircuitTransaction::from(payout_tx.clone());
            let op_return_output = get_first_op_return_output(&circuit_payout_tx);

            // If OP_RETURN doesn't exist in any outputs, or the data in OP_RETURN is not a valid xonly_pubkey,
            // operator_xonly_pk will be set to None, and the corresponding column in DB set to NULL.
            // This can happen if optimistic payout is used, or an operator constructs the payout tx wrong.
            let operator_xonly_pk = op_return_output
                .and_then(|output| parse_op_return_data(&output.script_pubkey))
                .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());
```

**File:** core/src/verifier.rs (L2337-2350)
```rust
            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
        }

        self.db
            .update_payout_txs_and_payer_operator_xonly_pk(
                Some(dbtx),
                payout_txs_and_payer_operator_idx,
            )
            .await?;
```

**File:** core/src/operator.rs (L620-637)
```rust
        let payout_txhandler = builder::transaction::create_payout_txhandler(
            input_utxo,
            output_txout,
            self.signer.xonly_public_key,
            in_signature,
            self.config.protocol_paramset().network,
        )?;

        // tracing::info!("Payout txhandler: {:?}", hex::encode(bitcoin::consensus::serialize(&payout_txhandler.get_cached_tx())));

        let sighash = payout_txhandler.calculate_sighash_txin(0, in_signature.sighash_type)?;

        SECP.verify_schnorr(
            &in_signature.signature,
            &Message::from_digest(*sighash.as_byte_array()),
            user_xonly_pk,
        )
        .wrap_err("Failed to verify signature received from user for payout txin. Ensure the signature uses SinglePlusAnyoneCanPay sighash type.")?;
```
