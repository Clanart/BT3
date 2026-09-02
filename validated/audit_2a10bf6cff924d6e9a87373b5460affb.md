Confirmed: the user's withdrawal signature uses `TapSighashType::SinglePlusAnyoneCanPay`, which commits to the single output at the same index (the user payout output) but allows other inputs to be freely modified/added by whoever broadcasts the transaction (`ANYONECANPAY`). However, this only covers input 0's signature — the output at index 0 (user's payout) is committed by SIGHASH_SINGLE, but outputs 1 (anchor) and 2 (OP_RETURN with operator pubkey) in `create_payout_txhandler` are *not* covered by `SIGHASH_SINGLE` since that only commits to the output with the same index as the input (index 0). This means the OP_RETURN operator attribution (output index 2) is malleable by anyone who intercepts the user's off-chain signature, since `SIGHASH_SINGLE` does not commit to outputs beyond the corresponding index, and `ANYONECANPAY` permits arbitrary additional inputs. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

This directly parallels the LienToken bug class: the "attribution" of who fronted the payout (embedded in the OP_RETURN, output index 2) is not cryptographically bound by the user's signature (which only commits to output index 0 under `SIGHASH_SINGLE`), so it can be reassigned/rewritten by a third party front-running the broadcast — exactly like the mutable `ownerOf(id)` binding in the original finding, where a value meant to identify "who should be credited" can be changed after the fact by someone other than the original committer.

### Title
Payout OP_RETURN operator attribution is not covered by the user's `SinglePlusAnyoneCanPay` signature, allowing misattribution of reimbursement credit - (File: core/src/builder/transaction/operator_reimburse.rs)

### Summary
The user's withdrawal authorization signature uses `TapSighashType::SinglePlusAnyoneCanPay`, which per BIP-341 only commits to the single output whose index matches the signing input (index 0, the user's payout output) and allows arbitrary other inputs to be added (`ANYONECANPAY`). `create_payout_txhandler` places the operator-attribution OP_RETURN at output index 2, and the anchor at output index 1 — neither is covered by the `SIGHASH_SINGLE` commitment. Consequently, an attacker (including any node that observes the user's off-chain signature, e.g. an operator, relayer, or eavesdropper) can construct and broadcast a different payout transaction that reuses the user's valid signature on input 0, keeps the same output 0, but substitutes a different operator's x-only pubkey in the OP_RETURN output (or removes/reorders outputs), or spends the same authorization while attaching different additional inputs/fees.

### Finding Description
`create_payout_txhandler` builds the payout transaction with the user-payout output at index 0, an anchor output at index 1, and an OP_RETURN output at index 2 containing the operator's x-only public key used later to attribute credit for the reimbursement (`payout_payer_operator_xonly_pk`) via `update_finalized_payouts` / `update_payout_txs_and_payer_operator_xonly_pk`. [1](#0-0) 

The operator's `withdraw` RPC handler verifies the user's signature strictly against the sighash of input 0 for the constructed transaction — it does not additionally constrain that no other party can construct an alternative transaction reusing the same signature with a different operator's OP_RETURN, because `SIGHASH_SINGLE|ANYONECANPAY` semantics do not bind outputs beyond index 0, nor other transaction inputs. [5](#0-4) 

The sighash type is explicitly required to be `SinglePlusAnyoneCanPay` by the parser. [2](#0-1) 

Later, `update_finalized_payouts` reads whichever payout transaction actually confirms on-chain, and blindly parses the OP_RETURN's embedded pubkey as the attributed payer/operator, without any additional cryptographic binding between the user's authorization and that specific operator pubkey. [6](#0-5) 

`is_kickoff_malicious` then trusts this attribution to decide whether a kickoff by a given operator is legitimate versus malicious. [7](#0-6) 

This breaks the intended equality: `operator attributed for reimbursement == operator who actually fronted the withdrawal (funded output 0 via broadcast)`. Because the OP_RETURN (the sole binding of that equality) is not covered by the sighash, whichever party broadcasts a version of the transaction with the user's reused signature determines the attributed operator, not necessarily the one who actually paid the withdrawal fee/provided liquidity.

### Impact Explanation
If a party other than the legitimate fronting operator can reuse the user's signature to attribute the withdrawal payout to a different (or no) operator xonly pubkey, this can result in: an operator being credited (and later reimbursed via the kickoff/round flow) for a payout it never actually funded, or the legitimate operator who did front the payout being unable to correctly attribute/kickoff for reimbursement because the on-chain payout's OP_RETURN was altered by another party's broadcast. This matches the Critical impact category: "an operator reimbursed for a payout it never funded" / "an honest operator permanently unable to be reimbursed."

### Likelihood Explanation
Exploitation requires observing the user's off-chain `SinglePlusAnyoneCanPay` signature (which is exchanged with operators off-chain per the documented flow) and racing to broadcast a modified transaction before the legitimate operator's version confirms. Since the signature is explicitly shared with operators (and potentially visible to other unprivileged network observers if broadcast/relayed), and Bitcoin `ANYONECANPAY`+`SINGLE` malleability is well understood, this is a realistic front-running/replace scenario requiring no privileged role — any party that learns the signature (e.g., a competing unprivileged operator or a mempool observer) can attempt this.

### Recommendation
Use a full-commitment sighash (e.g., `SIGHASH_ALL` or otherwise ensure the OP_RETURN operator-attribution output is bound to the same signature) instead of `SIGHASH_SINGLE|ANYONECANPAY`, or otherwise cryptographically bind the intended operator pubkey into the data the user signs, so that the attributed operator cannot be altered by a third party reusing the authorization.

### Proof of Concept
1. User signs a withdrawal payout with `TapSighashType::SinglePlusAnyoneCanPay` over `input_outpoint`, `output_script_pubkey`, and `output_amount`, and shares this with Operator A via the `withdraw` RPC flow (per protocol design). [8](#0-7) 
2. An attacker (e.g. a competing entity that observes this signature, such as Operator B, or any relayer) constructs an alternate transaction using `create_payout_txhandler` with the same input/output 0, but with a different `operator_xonly_pk` in the OP_RETURN (or omits it) — the `SIGHASH_SINGLE|ANYONECANPAY` signature remains valid because it does not cover output index 2 or additional inputs. [9](#0-8) 
3. Attacker broadcasts this alternate transaction before Operator A's version confirms.
4. `update_finalized_payouts` parses the OP_RETURN of whichever transaction actually confirms, attributing `payout_payer_operator_xonly_pk` accordingly, even though Operator A is the one whose funds actually paid for output 0 in economic terms if fee-funding overlaps, or the withdrawal is now attributed to the wrong/no operator. [10](#0-9)

### Citations

**File:** core/src/builder/transaction/operator_reimburse.rs (L407-436)
```rust
pub fn create_payout_txhandler(
    input_utxo: UTXO,
    output_txout: TxOut,
    operator_xonly_pk: XOnlyPublicKey,
    user_sig: taproot::Signature,
    _network: bitcoin::Network,
) -> Result<TxHandler<Signed>, BridgeError> {
    let txin = SpendableTxIn::new_partial(input_utxo.outpoint, input_utxo.txout);

    let output_txout = UnspentTxOut::from_partial(output_txout.clone());

    let op_return_txout = op_return_txout(PushBytesBuf::from(operator_xonly_pk.serialize()));

    let mut txhandler = TxHandlerBuilder::new(TransactionType::Payout)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            txin,
            SpendPath::KeySpend,
            DEFAULT_SEQUENCE,
        )
        .add_output(output_txout)
        .add_output(UnspentTxOut::from_partial(anchor_output(
            NON_EPHEMERAL_ANCHOR_AMOUNT,
        )))
        .add_output(UnspentTxOut::from_partial(op_return_txout))
        .finalize();
    txhandler.set_p2tr_key_spend_witness(&user_sig, 0)?;
    txhandler.promote()
}
```

**File:** core/src/rpc/parser/operator.rs (L161-187)
```rust
#[allow(clippy::result_large_err)]
pub fn parse_withdrawal_sig_params(
    params: WithdrawParams,
) -> Result<(u32, taproot::Signature, OutPoint, ScriptBuf, Amount), Status> {
    let mut input_signature =
        taproot::Signature::from_slice(&params.input_signature).map_err(|e| {
            Status::invalid_argument(format!("Can't convert input to taproot Signature - {e}"))
        })?;

    // If the Taproot sighash type is Default (no explicit type attached; i.e. a 64-byte
    // signature without a sighash flag), normalize it to SinglePlusAnyoneCanPay.
    // Prior to v0.5 this was Clementine's implicit behavior; we retain it here for
    // backwards compatibility when a 64-byte signature is provided.
    if input_signature.sighash_type == TapSighashType::Default {
        tracing::warn!(
            "Input signature for withdrawal {} has sighash type default, setting to SinglePlusAnyoneCanPay", params.withdrawal_id,
        );
        input_signature.sighash_type = TapSighashType::SinglePlusAnyoneCanPay;
    }

    // enforce sighash type here
    if input_signature.sighash_type != TapSighashType::SinglePlusAnyoneCanPay {
        return Err(Status::invalid_argument(format!(
            "Input signature has wrong sighash type, SinglePlusAnyoneCanPay expected, got {}",
            input_signature.sighash_type
        )));
    }
```

**File:** core/src/operator.rs (L614-637)
```rust
        let user_xonly_pk = &input_utxo
            .txout
            .script_pubkey
            .try_get_taproot_pk()
            .wrap_err("Input utxo script pubkey is not a valid taproot script")?;

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

**File:** core/src/verifier.rs (L1882-1914)
```rust
        let Some(operator_xonly_pk) = operator_xonly_pk_opt else {
            tracing::warn!("No operator xonly pk found in payout tx OP_RETURN, assuming malicious");
            return Ok(true);
        };

        if operator_xonly_pk != kickoff_data.operator_xonly_pk {
            tracing::warn!("Operator xonly pk for the payout does not match with the kickoff_data");
            return Ok(true);
        }

        let wt_derive_path = WinternitzDerivationPath::Kickoff(
            kickoff_data.round_idx,
            kickoff_data.kickoff_idx,
            self.config.protocol_paramset(),
        );
        let commits = extract_winternitz_commits(
            kickoff_witness,
            &[wt_derive_path],
            self.config.protocol_paramset(),
        )?;
        let blockhash_data = commits.first();
        // only last 20 bytes of the blockhash is committed
        let truncated_blockhash = &payout_blockhash[12..];
        if let Some(committed_blockhash) = blockhash_data {
            if committed_blockhash != truncated_blockhash {
                tracing::warn!("Payout blockhash does not match committed hash: committed: {:?}, truncated payout blockhash: {:?}",
                        blockhash_data, truncated_blockhash);
                return Ok(true);
            }
        } else {
            return Err(eyre::eyre!("Couldn't retrieve committed data from witness").into());
        }
        Ok(false)
```

**File:** core/src/verifier.rs (L2311-2342)
```rust
            let payout_tx = &block.txdata[*payout_tx_idx];
            // Find the first output that contains OP_RETURN
            let circuit_payout_tx = CircuitTransaction::from(payout_tx.clone());
            let op_return_output = get_first_op_return_output(&circuit_payout_tx);

            // If OP_RETURN doesn't exist in any outputs, or the data in OP_RETURN is not a valid xonly_pubkey,
            // operator_xonly_pk will be set to None, and the corresponding column in DB set to NULL.
            // This can happen if optimistic payout is used, or an operator constructs the payout tx wrong.
            let operator_xonly_pk = op_return_output
                .and_then(|output| parse_op_return_data(&output.script_pubkey))
                .and_then(|bytes| XOnlyPublicKey::from_slice(bytes).ok());

            if operator_xonly_pk.is_none() {
                tracing::info!(
                    "No valid operator xonly pk found in payout tx {:?} OP_RETURN. Either it is an optimistic payout or the operator constructed the payout tx wrong",
                    payout_txid
                );
            }

            tracing::info!(
                "A new payout tx detected for withdrawal {}, payout txid: {:?}, operator xonly pk: {:?}",
                idx,
                payout_txid,
                operator_xonly_pk
            );

            payout_txs_and_payer_operator_idx.push((
                idx,
                payout_txid,
                operator_xonly_pk,
                block_hash,
            ));
```

**File:** core/src/rpc/clementine.rs (L238-258)
```rust
#[derive(Clone, PartialEq, ::prost::Message)]
pub struct WithdrawParams {
    /// The ID of the withdrawal in Citrea
    #[prost(uint32, tag = "1")]
    pub withdrawal_id: u32,
    /// User's \[`bitcoin::sighash::TapSighashType::SinglePlusAnyoneCanPay`\]
    /// signature
    #[prost(bytes = "vec", tag = "2")]
    pub input_signature: ::prost::alloc::vec::Vec<u8>,
    /// User's UTXO to claim the deposit
    #[prost(message, optional, tag = "3")]
    pub input_outpoint: ::core::option::Option<Outpoint>,
    /// The withdrawal output's script_pubkey (user's signature is only valid for
    /// this pubkey)
    #[prost(bytes = "vec", tag = "4")]
    pub output_script_pubkey: ::prost::alloc::vec::Vec<u8>,
    /// The withdrawal output's amount (user's signature is only valid for this
    /// amount)
    #[prost(uint64, tag = "5")]
    pub output_amount: u64,
}
```
