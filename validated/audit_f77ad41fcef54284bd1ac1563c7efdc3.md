### Title
Challenge Transaction `challenger_evm_address` Not Bound by `SIGHASH_SINGLE|ANYONECANPAY` Signature Enables Front-Running to Steal Citrea Reimbursement - (File: `core/src/builder/transaction/challenge.rs`)

---

### Summary

The `Challenge` Bitcoin transaction embeds the challenger's EVM address in an `OP_RETURN` output so the Citrea bridge contract can reimburse the challenger if the operator is proven malicious. However, the transaction's only input is signed with `SIGHASH_SINGLE | ANYONECANPAY`, which commits to the input and to output 0 (operator reimbursement) but **not** to output 1 (the `OP_RETURN` carrying `challenger_evm_address`). Any mempool observer can copy the pre-signed input, substitute their own EVM address in the `OP_RETURN`, and broadcast a higher-fee replacement transaction, stealing the Citrea-side reimbursement from the legitimate challenger.

---

### Finding Description

`create_challenge_txhandler` builds the Challenge transaction with two outputs:

```
Output 0: operator reimbursement (operator_challenge_amount)
Output 1: OP_RETURN(challenger_evm_address)   ← NOT covered by signature
``` [1](#0-0) 

The input's signature kind is `NormalSignatureKind::Challenge`, which resolves to `OperatorSharedDeposit(SinglePlusAnyoneCanPay)`: [2](#0-1) 

Under BIP-341, `SIGHASH_SINGLE | ANYONECANPAY` commits only to the single input being spent and to the output **at the same index** (output 0). Output 1 — the `OP_RETURN` containing `challenger_evm_address` — is entirely outside the signed digest.

The `challenger_evm_address` is populated from the verifier's own signer at transaction-creation time: [3](#0-2) 

When the verifier's automation detects a malicious kickoff, it queues the pre-built Challenge transaction for broadcast: [4](#0-3) 

Once the transaction enters the Bitcoin mempool it is public. Because the operator's `SinglePlusAnyoneCanPay` signature is already embedded in the witness, any observer can:

1. Extract the valid signature from the mempool transaction.
2. Construct a new transaction spending the same `UtxoVout::Challenge` UTXO with the same signature.
3. Replace output 1 with an `OP_RETURN` containing the attacker's own EVM address.
4. Broadcast at a higher fee rate, evicting the legitimate transaction.

The Citrea bridge contract reads the `OP_RETURN` EVM address from the on-chain Challenge transaction to determine who receives the operator-collateral reimbursement. With the attacker's address substituted, the legitimate challenger receives nothing.

---

### Impact Explanation

The challenger pays `operator_challenge_amount` (configured at 2 BTC in the reference paramset) as the cost of challenging. If the challenge is correct and the operator is subsequently disproved, the Citrea bridge contract is supposed to reimburse the challenger's EVM address from the operator's locked collateral. Front-running replaces that address with the attacker's, permanently redirecting the reimbursement. The legitimate challenger bears the full on-chain cost of the challenge and receives zero compensation — a direct, material loss of bridged-BTC-equivalent collateral.

---

### Likelihood Explanation

The attack requires only passive mempool monitoring and the ability to construct a standard Bitcoin transaction. No privileged key material is needed beyond what is already visible in the mempool transaction witness. The window is the normal block-confirmation interval. Any party running a Bitcoin node can execute this.

---

### Recommendation

Change the sighash type for the Challenge input from `SIGHASH_SINGLE | ANYONECANPAY` to `SIGHASH_ALL` (or at minimum `SIGHASH_ALL | ANYONECANPAY`). `SIGHASH_ALL` commits to every output, including the `OP_RETURN`, making it impossible to substitute a different EVM address without invalidating the signature.

In `deposit_signature_owner.rs`, change:

```rust
Challenge => Ok(OperatorSharedDeposit(SinglePlusAnyoneCanPay)),
```

to:

```rust
Challenge => Ok(OperatorSharedDeposit(SighashDefault)),  // or AllPlusAnyoneCanPay
``` [2](#0-1) 

This binds the operator's pre-signature to the exact `challenger_evm_address` chosen at deposit time, preventing any post-hoc substitution.

---

### Proof of Concept

1. Operator and verifiers complete the deposit-signing ceremony. The operator signs the Challenge input with `SIGHASH_SINGLE | ANYONECANPAY`; the verifier's EVM address `0xVERIFIER` is embedded in output 1.
2. A malicious kickoff is detected. The verifier's automation calls `handle_kickoff`, which queues and broadcasts the Challenge transaction containing `OP_RETURN(0xVERIFIER)`.
3. Attacker observes the transaction in the mempool, extracts the operator's Schnorr signature `sig_op` from `input[0].witness`.
4. Attacker constructs `challenge_tx_evil`:
   - `input[0]`: same `UtxoVout::Challenge` outpoint, witness = `[sig_op, script, control_block]`
   - `output[0]`: operator reimbursement (identical to original — covered by `SIGHASH_SINGLE`)
   - `output[1]`: `OP_RETURN(0xATTACKER)` — **not** covered by `sig_op`
5. Attacker broadcasts `challenge_tx_evil` with fee rate > original. Bitcoin miners include it first.
6. The legitimate challenge transaction is rejected as a double-spend.
7. The Citrea bridge contract reads `0xATTACKER` from the on-chain `OP_RETURN` and routes the operator-collateral reimbursement to the attacker. The verifier who detected and reported the malicious kickoff receives nothing.

### Citations

**File:** core/src/builder/transaction/challenge.rs (L326-345)
```rust
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

**File:** core/src/builder/transaction/deposit_signature_owner.rs (L79-79)
```rust
                    Challenge => Ok(OperatorSharedDeposit(SinglePlusAnyoneCanPay)),
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

**File:** core/src/verifier.rs (L2005-2016)
```rust
                #[cfg(feature = "automation")]
                self.tx_sender
                    .add_tx_to_queue(
                        dbtx,
                        TransactionType::Challenge,
                        &challenge_tx,
                        &[],
                        Some(tx_metadata),
                        self.config.protocol_paramset(),
                        None,
                    )
                    .await?;
```
