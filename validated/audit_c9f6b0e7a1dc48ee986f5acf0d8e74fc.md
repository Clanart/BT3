### Title
`Verifier::is_deposit_valid` accepts self-funded UTXOs as "replacement deposits" without verifying the claimed `old_move_txid` was ever actually spent/replaced - ([File: core/src/verifier.rs])

### Summary
For `DepositType::ReplacementDeposit`, `Verifier::is_deposit_valid` only checks that the `deposit_outpoint`'s own `TxOut` has `value == bridge_amount` and `script_pubkey == expected_scriptpubkey` (derived purely from the public `old_move_txid`, `nofn_xonly_pk`, and `security_council` fields). It never checks that the transaction creating `deposit_outpoint` actually spends the claimed `old_move_txid`'s move-to-vault output (`UtxoVout::DepositInMove`) via the security-council/NofN script path. Since the replacement-deposit address is fully public and computable (`generate_replacement_deposit_address`), any unprivileged attacker can pay their own funds into it and register it as a "replacement" for any `old_move_txid`, including one that was never authorized for replacement.

### Finding Description
The binding that should hold is: `deposit_data.deposit_type == ReplacementDeposit{old_move_txid: M}` implies that the Bitcoin transaction funding `deposit_data.deposit_outpoint` has an **input** that spends `OutPoint{txid: M, vout: UtxoVout::DepositInMove}` through either `CheckSig(old_nofn_xonly_pk)` or `Multisig::from_security_council(...)` — i.e., that M was genuinely released via the security-council replacement path (as constructed by `create_replacement_deposit_txhandler`, [1](#0-0) ).

`Verifier::is_deposit_valid` never inspects the deposit transaction's inputs. It computes the expected output script purely from `deposit_data.get_deposit_scripts(...)`, which for `ReplacementDeposit` only depends on `(nofn_xonly_pk, old_move_txid)` and the security-council multisig — both public values — and then only checks the on-chain `TxOut` value/script and block height: [2](#0-1) 

The address `generate_replacement_deposit_address(old_move_txid, nofn_xonly_pk, network, security_council)` is fully derivable by anyone from public data (M, the aggregated NofN key, and the security council configuration), as seen in the CLI helper `GetReplacementDepositAddress`: [3](#0-2) 

`ReplacementDepositScript` itself only commits to `(nofn_xonly_pk, old_move_txid)` in the script, with no reference to whether M was ever actually spent: [4](#0-3) 

Exploit flow:
1. Attacker (or anyone) creates a real deposit that becomes move txid `M`, and it is fully settled through the normal payout path (Reimburse/emergency-stop), not via the security council. `M`'s move-to-vault UTXO is now spent through a path unrelated to `Multisig::from_security_council`.
2. Attacker computes `generate_replacement_deposit_address(M, nofn_xonly_pk, network, security_council)` and sends a self-funded UTXO `R` of exactly `bridge_amount` to it (no relation to `M`'s spend at all).
3. Attacker calls the aggregator's `NewReplacementDeposit` gRPC with `deposit_outpoint = R`, `old_move_txid = M`.
4. Verifiers run `is_deposit_valid` during `deposit_sign`/`deposit_finalize`; the checks at lines 659-705 pass because they never look at `R`'s parent transaction's inputs.
5. Verifiers proceed to produce N-of-N partial signatures for a new move-to-vault transaction for `R`, tagged in its script as "replacing" `M`, even though `M` was never released by the security council.

This defeats the sole purpose of the replacement mechanism, which is meant to gate the "replaces `M`" claim behind an actual security-council-authorized spend of `M`'s vault output.

### Impact Explanation
`Verifier::is_deposit_valid` is the only gate in the Bitcoin/verifier layer that is supposed to authenticate a "this deposit replaces `old_move_txid`" claim before the N-of-N signs off on it. Because the check is missing, an unprivileged attacker can obtain valid N-of-N signatures over a move-to-vault transaction that falsely asserts it replaces an arbitrary, already-settled `old_move_txid`. The entire point of tagging `old_move_txid` in the output script is for the downstream system (Citrea's `replaceDeposit`) to re-bind an existing `deposit_id`'s accounting to a new move txid based on this on-chain commitment; with no verifier-side authentication of the claim, this becomes forgeable by anyone able to fund a UTXO of `bridge_amount`, undermining the trust boundary the security-council replacement path was designed to enforce. This is repeatable against every settled deposit and does not require compromising any privileged role.

### Likelihood Explanation
No special privileges are needed: the attacker only needs to know a public move txid `M`, the public NofN aggregate key, and the public security council configuration (all discoverable), fund a UTXO with `bridge_amount`, and call the aggregator's public `NewReplacementDeposit`/`new_deposit` gRPC endpoint. Cost is one `bridge_amount` deposit plus fees. The check is missing unconditionally (not gated by any config/paramset), so it is deterministically reproducible.

### Recommendation
In `Verifier::is_deposit_valid` (core/src/verifier.rs), for `DepositType::ReplacementDeposit`, in addition to the output-script/value checks, verify that the transaction funding `deposit_outpoint` has an input spending `OutPoint{ txid: old_move_txid, vout: UtxoVout::DepositInMove }`, and that this input's witness satisfies either the NofN `CheckSig` or the `Multisig::from_security_council` script path (i.e., resolve and validate the actual spending transaction of `M`'s move-to-vault output, not just the destination script of the new UTXO).

### Proof of Concept
```rust
// cargo test proof outline (core/src/test/... or a new test module), no mainnet/no live Citrea:
// 1. Use run_single_deposit to create and fully confirm a real deposit; obtain move_txid M.
// 2. Settle M via the NORMAL path (Reimburse) rather than the security-council replacement script,
//    so M's DepositInMove vout is spent by a path other than Multisig::from_security_council.
// 3. Compute replacement address:
//    let (addr, _) = generate_replacement_deposit_address(M, nofn_xonly_pk, network, security_council)?;
// 4. Fund `addr` with a fresh, self-funded UTXO R (bridge_amount) unrelated to M (do NOT spend M's vault).
// 5. Build DepositInfo{ deposit_outpoint: R, deposit_type: ReplacementDeposit{ old_move_txid: M } }
//    and call verifier.is_deposit_valid(&mut deposit_data).
// 6. Assert:
//    assert!(result.is_err(), "expected rejection: M was never replaced via security council path");
//    // Current behavior: result is Ok(()) -- demonstrating the missing linkage check.
```

### Citations

**File:** core/src/builder/transaction/mod.rs (L404-428)
```rust
pub fn create_replacement_deposit_txhandler(
    old_move_txid: Txid,
    input_outpoint: OutPoint,
    old_nofn_xonly_pk: XOnlyPublicKey,
    new_nofn_xonly_pk: XOnlyPublicKey,
    paramset: &'static ProtocolParamset,
    security_council: SecurityCouncil,
) -> Result<TxHandler, BridgeError> {
    Ok(TxHandlerBuilder::new(TransactionType::ReplacementDeposit)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NoSignature,
            SpendableTxIn::from_scripts(
                input_outpoint,
                paramset.bridge_amount,
                vec![
                    Arc::new(CheckSig::new(old_nofn_xonly_pk)),
                    Arc::new(Multisig::from_security_council(security_council.clone())),
                ],
                None,
                paramset.network,
            ),
            crate::builder::script::SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
```

**File:** core/src/verifier.rs (L659-705)
```rust
        // check if deposit script in deposit_outpoint is valid
        let deposit_scripts: Vec<ScriptBuf> = deposit_data
            .get_deposit_scripts(self.config.protocol_paramset())?
            .into_iter()
            .map(|s| s.to_script_buf())
            .collect();
        // what the deposit scriptpubkey is in the deposit_outpoint should be according to the deposit data
        let expected_scriptpubkey = create_taproot_address(
            &deposit_scripts,
            None,
            self.config.protocol_paramset().network,
        )
        .0
        .script_pubkey();
        let deposit_outpoint = deposit_data.get_deposit_outpoint();
        let deposit_txid = deposit_outpoint.txid;
        let deposit_tx = self
            .rpc
            .get_tx_of_txid(&deposit_txid)
            .await
            .wrap_err("Deposit tx could not be found on chain")?;
        let deposit_txout_in_chain = deposit_tx
            .output
            .get(deposit_outpoint.vout as usize)
            .ok_or(eyre::eyre!(
                "Deposit vout not found in tx {}, vout: {}",
                deposit_txid,
                deposit_outpoint.vout
            ))?;
        if deposit_txout_in_chain.value != self.config.protocol_paramset().bridge_amount {
            let reason = format!(
                "Deposit amount is not correct, expected {}, got {}",
                self.config.protocol_paramset().bridge_amount,
                deposit_txout_in_chain.value
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        if deposit_txout_in_chain.script_pubkey != expected_scriptpubkey {
            let reason = format!(
                "Deposit script pubkey in deposit outpoint does not match the deposit data, expected {:?}, got {:?}",
                expected_scriptpubkey,
                deposit_txout_in_chain.script_pubkey
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
```

**File:** core/src/bin/cli.rs (L846-885)
```rust
        AggregatorCommands::GetReplacementDepositAddress {
            move_txid,
            network,
            security_council,
        } => {
            let mut move_txid = hex::decode(move_txid).expect("Failed to decode txid");
            move_txid.reverse();
            let move_txid = bitcoin::Txid::from_byte_array(
                move_txid
                    .try_into()
                    .expect("Failed to convert txid to array"),
            );

            let response = aggregator
                .get_nofn_aggregated_xonly_pk(Request::new(Empty {}))
                .await
                .expect("Failed to make a request");

            let nofn_xonly_pk =
                bitcoin::XOnlyPublicKey::from_slice(&response.get_ref().nofn_xonly_pk)
                    .expect("Failed to parse xonly_pk");

            let network = match network {
                Some(network) => {
                    bitcoin::Network::from_str(&network).expect("Failed to parse network")
                }
                None => bitcoin::Network::Regtest,
            };

            let (replacement_deposit_address, _) =
                clementine_core::builder::address::generate_replacement_deposit_address(
                    move_txid,
                    nofn_xonly_pk,
                    network,
                    security_council.expect("Security council is required"),
                )
                .expect("Failed to generate replacement deposit address");

            println!("Replacement deposit address: {replacement_deposit_address}");
        }
```

**File:** core/src/builder/script.rs (L512-539)
```rust
/// Struct for deposit script that replaces an old move tx with a replacement deposit (to update bridge design on chain)
/// It commits to the old move txid inside the script.
#[derive(Debug, Clone)]
pub struct ReplacementDepositScript(pub(crate) XOnlyPublicKey, Txid);

impl SpendableScript for ReplacementDepositScript {
    fn as_any(&self) -> &dyn Any {
        self
    }

    fn kind(&self) -> ScriptKind {
        ScriptKind::ReplacementDepositScript(self)
    }

    fn to_script_buf(&self) -> ScriptBuf {
        let citrea_replace: [u8; 13] = "citreaReplace".as_bytes().try_into().expect("length == 13");

        Builder::new()
            .push_x_only_key(&self.0)
            .push_opcode(OP_CHECKSIG)
            .push_opcode(OP_FALSE)
            .push_opcode(OP_IF)
            .push_slice(citrea_replace)
            .push_slice(self.1.as_byte_array())
            .push_opcode(OP_ENDIF)
            .into_script()
    }
}
```
