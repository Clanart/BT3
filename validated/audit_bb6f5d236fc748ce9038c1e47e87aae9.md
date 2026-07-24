### Title
Unprotected `challenger_evm_address` in OP_RETURN Enables Citrea Reimbursement Theft via `SIGHASH_SINGLE|ANYONECANPAY` — (`core/src/builder/transaction/challenge.rs`)

---

### Summary

The challenge transaction's operator pre-signature uses `SIGHASH_SINGLE | ANYONECANPAY`, which commits only to input 0 (the challenge UTXO) and output 0 (the operator's BTC reimbursement). Output 1 — the OP_RETURN carrying the `challenger_evm_address` that Citrea's bridge contract uses to route the operator-collateral reimbursement — is entirely outside the signature's scope. Any verifier holding the pre-signed operator signature can therefore construct a valid challenge transaction substituting their own EVM address, stealing the Citrea-side reimbursement from the legitimate challenger.

---

### Finding Description

During deposit setup, the operator pre-signs the challenge transaction with `SIGHASH_SINGLE | ANYONECANPAY` and distributes the signature to all verifiers. This is recorded in `deposit_signature_owner.rs`:

```
Challenge => Ok(OperatorSharedDeposit(SinglePlusAnyoneCanPay)),
``` [1](#0-0) 

The sighash calculation in `txhandler.rs` confirms that `SinglePlusAnyoneCanPay` uses `Prevouts::One`, covering only the single input's prevout and the corresponding output at the same index: [2](#0-1) 

The challenge transaction is constructed in `create_challenge_txhandler`:

- **Output 0** (`operator_challenge_amount` BTC to `operator_reimbursement_address`) — **covered** by the operator's `SIGHASH_SINGLE|ANYONECANPAY` signature.
- **Output 1** (OP_RETURN containing `challenger_evm_address.0`) — **not covered** by any signature. [3](#0-2) 

The `challenger_evm_address` is sourced from the signer's own key at construction time: [4](#0-3) 

Because the operator's signature does not commit to output 1, any party possessing the pre-signed operator signature can replace `challenger_evm_address` with an arbitrary EVM address and produce a Bitcoin-valid challenge transaction. All verifiers receive this pre-signed signature during deposit setup and store it in their databases. [5](#0-4) 

---

### Impact Explanation

The OP_RETURN in the challenge transaction is the on-chain record that Citrea's bridge contract reads to determine which EVM address receives the operator's slashed collateral when a challenge succeeds. A malicious verifier who races to submit the challenge transaction first — or who simply constructs it independently before the legitimate challenger — can embed their own EVM address in the OP_RETURN. If the challenge is correct (operator is malicious), the attacker receives the full Citrea-side reimbursement (operator collateral) while the legitimate challenger receives nothing. The BTC side (output 0) is unaffected because it is covered by the operator's signature, but the Citrea collateral reimbursement — the primary economic incentive for honest challengers — is fully redirectable. [6](#0-5) 

---

### Likelihood Explanation

All verifiers hold the pre-signed operator signature for the challenge transaction from deposit time. No mempool observation is required: any verifier can independently construct a competing challenge transaction with their own EVM address and submit it with a higher fee. The challenge UTXO can only be spent once, so the first transaction mined wins. Because the protocol has multiple verifiers and the economic incentive (operator collateral) is significant, the race is credible. The non-standard V3 transaction format reduces public-mempool exposure but does not prevent a verifier from submitting directly to a miner or using a private relay. [7](#0-6) 

---

### Recommendation

Replace `SIGHASH_SINGLE | ANYONECANPAY` with `SIGHASH_ALL` (or `SIGHASH_DEFAULT`) for the challenge transaction's operator signature. This forces the sighash to commit to all outputs, including the OP_RETURN carrying `challenger_evm_address`, making it impossible to substitute a different EVM address without invalidating the operator's signature.

Alternatively, derive the `challenger_evm_address` deterministically from the verifier's xonly public key (which is already committed in the deposit data) rather than accepting it as a free parameter at transaction-construction time, and verify this derivation inside the bridge circuit before routing the Citrea reimbursement. [1](#0-0) 

---

### Proof of Concept

1. During deposit setup, operator pre-signs the challenge transaction with `SIGHASH_SINGLE | ANYONECANPAY` and distributes the signature to all verifiers (stored in `deposit_signatures` DB table).

2. Legitimate verifier V1 constructs a challenge transaction:
   - Input 0: `UtxoVout::Challenge` from kickoff tx
   - Output 0: `operator_challenge_amount` → `operator_reimbursement_address` (covered by operator sig)
   - Output 1: OP_RETURN(`V1_evm_address`) (NOT covered by operator sig)
   - Broadcasts to Bitcoin network.

3. Malicious verifier V2, holding the same pre-signed operator signature, constructs:
   - Input 0: identical (same challenge UTXO)
   - Output 0: identical (same operator reimbursement — required for signature validity)
   - Output 1: OP_RETURN(`V2_evm_address`) — substituted freely
   - The operator's `SIGHASH_SINGLE | ANYONECANPAY` signature remains valid because it does not cover output 1.

4. V2 submits with a higher fee. V2's transaction is mined first.

5. The operator is subsequently disproved as malicious. Citrea's bridge contract reads the OP_RETURN from the on-chain challenge transaction and routes the operator's slashed collateral to `V2_evm_address`. V1 receives nothing despite having done the legitimate challenge work. [8](#0-7) [9](#0-8)

### Citations

**File:** core/src/builder/transaction/deposit_signature_owner.rs (L33-45)
```rust
pub enum DepositSigKeyOwner {
    NotOwned,
    /// Operator's signature required for deposit (shared with verifiers), with the given sighash type.
    OperatorSharedDeposit(TapSighashType),
    /// N-of-N signature required for deposit, with the given sighash type.
    NofnSharedDeposit(TapSighashType),
    /// Signature required for the entity itself, with the given sighash type.
    /// Verifiers do not need this signature info, thus it is not saved to DB.
    /// Added to help define different sighash types for operator's own signatures.
    Own(TapSighashType),
    /// Operator's signature required during first setup, with the given sighash type.
    OperatorSharedSetup(TapSighashType),
}
```

**File:** core/src/builder/transaction/deposit_signature_owner.rs (L79-79)
```rust
                    Challenge => Ok(OperatorSharedDeposit(SinglePlusAnyoneCanPay)),
```

**File:** core/src/builder/transaction/txhandler.rs (L222-233)
```rust
        let prevouts = match sighash_type {
            TapSighashType::SinglePlusAnyoneCanPay
            | TapSighashType::AllPlusAnyoneCanPay
            | TapSighashType::NonePlusAnyoneCanPay => {
                bitcoin::sighash::Prevouts::One(txin_index, prevouts_vec[txin_index])
            }
            _ => bitcoin::sighash::Prevouts::All(&prevouts_vec),
        };

        let sig_hash = sighash_cache
            .taproot_key_spend_signature_hash(txin_index, &prevouts, sighash_type)
            .wrap_err("Failed to calculate taproot sighash for key spend")?;
```

**File:** core/src/builder/transaction/challenge.rs (L296-345)
```rust
/// Creates a [`TxHandler`] for the `challenge` transaction.
///
/// This transaction is used to reimburse an operator for a valid challenge, intended to cover their costs for sending asserts transactions,
/// and potentially cover their opportunity cost as their reimbursements are delayed due to the challenge. This cost of a challenge is also
/// used to disincentivize sending challenges for kickoffs that are correct. In case the challenge is correct and operator is proved to be
/// malicious, the challenge cost will be reimbursed using the operator's collateral that's locked in Citrea.
///
/// # Inputs
/// 1. KickoffTx: Challenge utxo
///
/// # Outputs
/// 1. Operator reimbursement output
/// 2. OP_RETURN output (containing EVM address of the challenger, for reimbursement if the challenge is correct)
///
/// # Arguments
///
/// * `kickoff_txhandler` - The kickoff transaction handler that the challenge belongs to.
/// * `operator_reimbursement_address` - The address to reimburse the operator to cover their costs.
/// * `challenger_evm_address` - The EVM address of the challenger, for reimbursement if the challenge is correct.
/// * `paramset` - Protocol parameter set.
///
/// # Returns
///
/// A [`TxHandler`] for the challenge transaction, or a [`BridgeError`] if construction fails.
pub fn create_challenge_txhandler(
    kickoff_txhandler: &TxHandler,
    operator_reimbursement_address: &bitcoin::Address,
    challenger_evm_address: Option<EVMAddress>,
    paramset: &'static ProtocolParamset,
) -> Result<TxHandler, BridgeError> {
    let mut builder = TxHandlerBuilder::new(TransactionType::Challenge)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::Challenge,
            kickoff_txhandler.get_spendable_output(UtxoVout::Challenge)?,
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(UnspentTxOut::from_partial(TxOut {
            value: paramset.operator_challenge_amount,
            script_pubkey: operator_reimbursement_address.script_pubkey(),
        }));

    if let Some(challenger_evm_address) = challenger_evm_address {
        builder = builder.add_output(UnspentTxOut::from_partial(op_return_txout(
            challenger_evm_address.0,
        )));
    }

    Ok(builder.finalize())
```

**File:** core/src/builder/transaction/creator.rs (L883-888)
```rust
    let challenge_txhandler = builder::transaction::create_challenge_txhandler(
        get_txhandler(&txhandlers, TransactionType::Kickoff)?,
        &operator_data.reimburse_addr,
        context.signer.map(|s| s.get_evm_address()).transpose()?,
        paramset,
    )?;
```

**File:** core/src/verifier.rs (L2157-2201)
```rust
    async fn queue_watchtower_challenge(
        &self,
        kickoff_data: KickoffData,
        deposit_data: DepositData,
        commit_data: Vec<u8>,
        dbtx: DatabaseTransaction<'_>,
    ) -> Result<(), BridgeError> {
        let (tx_type, challenge_tx) = self
            .create_watchtower_challenge(
                TransactionRequestData {
                    deposit_outpoint: deposit_data.get_deposit_outpoint(),
                    kickoff_data,
                },
                &commit_data,
                Some(dbtx),
            )
            .await?;

        #[cfg(feature = "automation")]
        {
            self.tx_sender
                .add_tx_to_queue(
                    dbtx,
                    tx_type,
                    &challenge_tx,
                    &[],
                    Some(TxMetadata {
                        tx_type,
                        operator_xonly_pk: Some(kickoff_data.operator_xonly_pk),
                        round_idx: Some(kickoff_data.round_idx),
                        kickoff_idx: Some(kickoff_data.kickoff_idx),
                        deposit_outpoint: Some(deposit_data.get_deposit_outpoint()),
                    }),
                    self.config.protocol_paramset(),
                    None,
                )
                .await?;

            tracing::info!(
                "Committed watchtower challenge, commit data: {:?}",
                commit_data
            );
        }

        Ok(())
```
