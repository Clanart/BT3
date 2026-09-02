### Title
Verifier `is_deposit_valid` never checks that a `ReplacementDeposit`'s funding UTXO actually spent `old_move_txid`'s vault output, letting an attacker obtain N-of-N presigned signatures for an unauthorized "replacement" - ([File: core/src/verifier.rs])

### Summary
`Verifier::is_deposit_valid` validates a `ReplacementDeposit` purely by recomputing the expected taproot `scriptPubkey` from `DepositType::ReplacementDeposit{old_move_txid}` and comparing it against the on-chain `deposit_outpoint`'s script and value. It never inspects the transaction that produced `deposit_outpoint` to confirm it actually spent `old_move_txid`'s `UtxoVout::DepositInMove` output via the `CheckSig(old_nofn_xonly_pk)`/`Multisig(security_council)` authorization path defined by `create_replacement_deposit_txhandler`. Because `ReplacementDepositScript`/`Multisig` addresses are fully deterministic from public data (`nofn_xonly_pk`, `old_move_txid`, `security_council`), anyone can compute and self-fund that address, making the "replacement" appear legitimate to the verifier while never touching the referenced old vault.

### Finding Description
The intended binding is: `old_move_txid` embedded in `ReplacementDepositScript` == the txid of a move-to-vault transaction whose `UtxoVout::DepositInMove` output was actually consumed as an input of the transaction that created `deposit_outpoint` (enforced on the input side of `create_replacement_deposit_txhandler` via `CheckSig::new(old_nofn_xonly_pk)` + `Multisig::from_security_council(...)`, [1](#0-0) ).

`Verifier::is_deposit_valid`, however, only checks:
- security council equality, actor uniqueness, operator collateral consistency, [2](#0-1) 
- that `deposit_outpoint`'s output `script_pubkey` equals `create_taproot_address(get_deposit_scripts(..))` and its `value` equals `bridge_amount`, [3](#0-2) 
- that the containing block height is `>= start_height`. [4](#0-3) 

It never fetches or inspects the inputs of the transaction identified by `deposit_outpoint.txid` (i.e. it never checks that this transaction actually spent `old_move_txid:UtxoVout::DepositInMove`, nor that the spend was authorized by the old N-of-N key or the security council multisig). `get_deposit_scripts` for `DepositType::ReplacementDeposit` simply re-derives `ReplacementDepositScript::new(nofn_xonly_pk, replacement_deposit_data.old_move_txid)` from attacker-supplied `old_move_txid`, [5](#0-4) , and `generate_replacement_deposit_address`/the script encoding show that `old_move_txid` is embedded as inert pushed data inside an `OP_FALSE OP_IF ... OP_ENDIF` branch, not enforced by any Script-level equality to a spent input, [6](#0-5) [7](#0-6) .

Exploit flow:
1. Attacker picks any real, confirmed move-to-vault txid `T` (public chain data) whose vault output is still unspent.
2. Attacker computes `addr = create_taproot_address([ReplacementDepositScript::new(nofn_xonly_pk, T), Multisig::from_security_council(security_council)])` — all inputs are public (aggregated N-of-N key and security council config are known/queryable).
3. Attacker funds `addr` with a self-owned UTXO of exactly `bridge_amount`, with no cooperation from the security council or old signer set.
4. Attacker calls `Aggregator::new_deposit` with `Deposit{deposit_outpoint: <self-funded outpoint>, deposit_data: ReplacementDeposit{old_move_txid: T}}` over the public gRPC.
5. Each `Verifier::deposit_sign`/`deposit_finalize` calls `is_deposit_valid`, which passes because script/value/height checks succeed — the missing input-spend check is never performed. [8](#0-7) [9](#0-8) 
6. Verifiers proceed to produce N-of-N MuSig2 partial signatures for a new move-to-vault transaction spending the attacker's self-funded UTXO — a spend whose claimed relationship to `T` was never authorized by anyone who could actually move `T`'s vault funds.

No existing guard (security council equality check, actor-uniqueness checks, RPC amount/script/height checks) verifies the spend-authorization binding; none of them call the Bitcoin RPC to check whether `T`'s `DepositInMove` output is unspent or whether `deposit_outpoint`'s parent transaction's inputs reference `T`.

### Impact Explanation
This directly matches the listed Critical category "N-of-N partial signatures for an unauthorised spend": the verifiers cooperatively sign a move-to-vault transaction for a "replacement" deposit that was never authorized to replace anything, using self-funded coins that have no cryptographic link to the claimed `old_move_txid`. Any downstream party who later feeds this presigned move tx and its `old_move_txid` script commitment to the Citrea bridge accounting could cause the deposit slot tied to `T` to be treated as replaced/superseded while `T`'s original vault UTXO remains completely untouched and independently redeemable — a double-count of custody entries backed by only one real vault UTXO of `bridge_amount`, with a second bridge_amount vault entry created "for free" from an unrelated, unauthorized funding source. This is repeatable for every confirmed `move_to_vault` txid on chain (all public), scales per attacker-funded UTXO, and is not limited to a single deposit or operator.

### Likelihood Explanation
Preconditions are minimal: the attacker needs to know any real move_to_vault txid (public chain data), be able to compute the deterministic replacement-deposit taproot address (all inputs public), and be able to fund that address with `bridge_amount` BTC plus fees — no verifier, operator, security council, or aggregator privilege is required, and the attack is reachable purely through public gRPC calls (`new_deposit`, `deposit_sign`/`deposit_finalize` via aggregator). Cost is one bridge_amount UTXO (which the attacker gets to keep control over up to signing) plus fees; this is entirely feasible and repeatable.

### Recommendation
In `Verifier::is_deposit_valid`, for `DepositType::ReplacementDeposit`, fetch the transaction that created `deposit_outpoint`, verify that one of its inputs is exactly `OutPoint{ txid: old_move_txid, vout: UtxoVout::DepositInMove }`, and verify that input's witness satisfies the `CheckSig(old_nofn_xonly_pk)` or `Multisig(security_council)` spend path (i.e., that it was actually the security-council/old-N-of-N–authorized `replacement_deposit_tx`). Additionally confirm that `T`'s `DepositInMove` output is indeed spent (not just referenced) before treating the new deposit as a valid replacement.

### Proof of Concept
```rust
// core/src/test/... (regtest, feature = "automation")
// 1. run_single_deposit(...) -> obtain a real, confirmed move_txid T with its vault
//    output at UtxoVout::DepositInMove still unspent.
// 2. Compute addr = generate_replacement_deposit_address(T, nofn_xonly_pk, network, security_council).
// 3. Self-fund `addr` directly via rpc.send_to_address (NOT via
//    create_replacement_deposit_txhandler / send_replacement_deposit_tx, i.e. skip
//    spending T's DepositInMove output entirely).
// 4. Build DepositInfo { deposit_outpoint: <self-funded outpoint>,
//        deposit_type: DepositType::ReplacementDeposit(ReplacementDepositData{ old_move_txid: T }) }.
// 5. Call aggregator.new_deposit(deposit) -> assert it returns Ok (move tx signatures produced),
//    instead of an InvalidDeposit/BridgeError.
// 6. Assert rpc.get_tx_out(&T, DepositInMove_vout) is still Some(..) (unspent),
//    proving old_move_txid's vault output was never touched, yet verifiers signed
//    a "replacement" move tx referencing it.
```
This demonstrates that `is_deposit_valid` succeeds despite the equality `deposit_outpoint's parent tx input == OutPoint{T, DepositInMove}` never holding, breaking the intended authorization binding.

### Citations

**File:** core/src/builder/transaction/mod.rs (L412-428)
```rust
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

**File:** core/src/verifier.rs (L541-601)
```rust
    async fn is_deposit_valid(&self, deposit_data: &mut DepositData) -> Result<(), BridgeError> {
        // check if security council is the same as in our config
        if deposit_data.security_council != self.config.security_council {
            let reason = format!(
                "Security council in deposit is not the same as in the config, expected {:?}, got {:?}",
                self.config.security_council,
                deposit_data.security_council
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        // check if extra watchtowers (non verifier watchtowers) are not greater than the maximum allowed
        if deposit_data.actors.watchtowers.len() > MAX_EXTRA_WATCHTOWERS {
            let reason = format!(
                "Number of extra watchtowers in deposit is greater than the maximum allowed, expected at most {}, got {}",
                MAX_EXTRA_WATCHTOWERS,
                deposit_data.actors.watchtowers.len()
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        // check if total watchtowers are not greater than the maximum allowed
        if deposit_data.get_num_watchtowers() > MAX_NUMBER_OF_WATCHTOWERS {
            let reason = format!(
                "Number of watchtowers in deposit is greater than the maximum allowed, expected at most {}, got {}",
                MAX_NUMBER_OF_WATCHTOWERS,
                deposit_data.get_num_watchtowers()
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }

        // check if all verifiers are unique
        if !deposit_data.are_all_verifiers_unique() {
            let reason = format!(
                "Verifiers in deposit are not unique: {:?}",
                deposit_data.actors.verifiers
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }

        // check if all watchtowers are unique
        if !deposit_data.are_all_watchtowers_unique() {
            let reason = format!(
                "Watchtowers in deposit are not unique: {:?}",
                deposit_data.actors.watchtowers
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }

        // check if all operators are unique
        if !deposit_data.are_all_operators_unique() {
            let reason = format!(
                "Operators in deposit are not unique: {:?}",
                deposit_data.actors.operators
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
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

**File:** core/src/verifier.rs (L706-731)
```rust
        // check if deposit outpoint is included in a block with height >= start_height
        let tx_info = self
            .rpc
            .get_raw_transaction_info(&deposit_txid, None)
            .await
            .wrap_err("Failed to get deposit transaction info")?;
        let blockhash = tx_info.blockhash.ok_or_else(|| {
            BridgeError::InvalidDeposit("Deposit transaction is not confirmed".to_string())
        })?;
        let block_height = self
            .rpc
            .get_block_info(&blockhash)
            .await
            .wrap_err(format!(
                "Failed to get block info for deposit tx block hash: {blockhash}",
            ))?
            .height;
        let start_height = self.config.protocol_paramset().start_height;
        if (block_height as u32) < start_height {
            let reason = format!(
                "Deposit transaction is included in a block with height {block_height} which is less than start_height {start_height}",
            );
            tracing::error!("{reason}");
            return Err(BridgeError::InvalidDeposit(reason));
        }
        Ok(())
```

**File:** core/src/verifier.rs (L866-898)
```rust
    pub async fn deposit_sign(
        &self,
        mut deposit_data: DepositData,
        session_id: u128,
        mut agg_nonce_rx: mpsc::Receiver<AggregatedNonce>,
    ) -> Result<mpsc::Receiver<Result<PartialSignature, BridgeError>>, BridgeError> {
        self.citrea_client
            .check_nofn_correctness(deposit_data.get_nofn_xonly_pk()?)
            .await?;

        self.is_deposit_valid(&mut deposit_data).await?;

        // set deposit data to db before starting to sign, ensures that if the deposit data already exists in db, it matches the one
        // given by the aggregator currently. We do not want to sign 2 different deposits for same deposit_outpoint
        self.db
            .insert_deposit_data_if_not_exists(
                None,
                &mut deposit_data,
                self.config.protocol_paramset(),
            )
            .await?;

        let verifier = self.clone();
        let (partial_sig_tx, partial_sig_rx) = mpsc::channel(constants::DEFAULT_CHANNEL_SIZE);
        let verifier_index = deposit_data.get_verifier_index(&self.signer.public_key)?;
        let verifiers_public_keys = deposit_data.get_verifiers();
        let monitor_sender = partial_sig_tx.clone();

        let deposit_blockhash = self
            .rpc
            .get_blockhash_of_tx(&deposit_data.get_deposit_outpoint().txid)
            .await?;

```

**File:** core/src/verifier.rs (L982-994)
```rust
    pub async fn deposit_finalize(
        &self,
        deposit_data: &mut DepositData,
        session_id: u128,
        mut sig_receiver: mpsc::Receiver<Signature>,
        mut agg_nonce_receiver: mpsc::Receiver<AggregatedNonce>,
        mut operator_sig_receiver: mpsc::Receiver<Signature>,
    ) -> Result<(PartialSignature, PartialSignature), BridgeError> {
        self.citrea_client
            .check_nofn_correctness(deposit_data.get_nofn_xonly_pk()?)
            .await?;

        self.is_deposit_valid(deposit_data).await?;
```

**File:** core/src/deposit.rs (L206-217)
```rust
            DepositType::ReplacementDeposit(replacement_deposit_data) => {
                let deposit_script: Arc<dyn SpendableScript> =
                    Arc::new(ReplacementDepositScript::new(
                        nofn_xonly_pk,
                        replacement_deposit_data.old_move_txid,
                    ));
                let security_council_script: Arc<dyn SpendableScript> = Arc::new(
                    Multisig::from_security_council(self.security_council.clone()),
                );

                Ok(vec![deposit_script, security_council_script])
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

**File:** core/src/builder/address.rs (L89-103)
```rust
pub fn generate_replacement_deposit_address(
    old_move_txid: bitcoin::Txid,
    nofn_xonly_pk: XOnlyPublicKey,
    network: bitcoin::Network,
    security_council: SecurityCouncil,
) -> Result<(Address, TaprootSpendInfo), BridgeError> {
    let deposit_script =
        ReplacementDepositScript::new(nofn_xonly_pk, old_move_txid).to_script_buf();

    let security_council_script = Multisig::from_security_council(security_council).to_script_buf();

    let (addr, spend) =
        create_taproot_address(&[deposit_script, security_council_script], None, network);
    Ok((addr, spend))
}
```
