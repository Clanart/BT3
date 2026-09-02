No vulnerability found for this question.

**Analysis supporting this conclusion:**

The claimed binding break rests on the assumption that an unauthenticated caller reaching `Aggregator::new_deposit` can force verifiers to sign a move-to-vault transaction whose output the attacker controls. Tracing the code shows this is false on two independent grounds:

1. **`new_deposit` is intentionally a public, unauthenticated entry point.** Its doc comment states it "Handles a new deposit request from a user," and the deposit workflow is designed so *any* depositor (by definition an unprivileged party) funds their own `deposit_outpoint` and calls this RPC to have verifiers move funds into the bridge covenant. `Interceptors::Noop` under `client_verification=false` is the documented, intended mode for exposing this RPC publicly — not a bypass of any authorization check. [1](#0-0) [2](#0-1) 

2. **The move-to-vault output is not attacker-controlled.** `create_move_to_vault_txhandler` always sends the `bridge_amount` output to an address built from `nofn_script` (N-of-N CheckSig) and `security_council_script`, entirely independent of who called `new_deposit` or what `recovery_taproot_address` they supplied. [3](#0-2) 

3. **`Verifier::is_deposit_valid` binds the deposit input strictly to the expected script, not to caller identity.** It reconstructs `expected_scriptpubkey` from `deposit_data.get_deposit_scripts(...)` (which embeds the real `nofn_xonly_pk`) and rejects the deposit unless the on-chain output's `script_pubkey` and `value` match exactly. [4](#0-3)  A malformed `recovery_taproot_address` would only affect the timelock recovery leaf (`get_deposit_scripts`, `TimelockScript`), and any malformation causing an invalid taproot address would fail parsing (`try_get_taproot_pk`) rather than let the attacker redirect the covenant output. [5](#0-4) 

Since the destination of the presigned move-to-vault spend is always the protocol-controlled N-of-N/security-council address — never something the attacker/depositor chooses — there is no divergence between the MINT_AUTHORITY binding before and after this call. The attacker gains nothing beyond what any legitimate depositor already gets: their own funds locked into the bridge covenant, later redeemable only via the normal withdrawal path (Citrea `withdraw` + payout flow), which is outside this call chain and unaffected by the absence of a TLS certificate on this specific public-facing RPC.

### Citations

**File:** core/src/rpc/aggregator.rs (L1430-1447)
```rust
    /// Handles a new deposit request from a user. This function coordinates the signing process
    /// between verifiers to create a valid move transaction. It ensures a covenant using pre-signed NofN transactions.
    /// It also collects signatures from operators to ensure that the operators can be slashed if they act maliciously.
    ///
    /// Overview:
    /// 1. Receive and parse deposit parameters from user
    /// 2. Signs all NofN transactions with verifiers using MuSig2:
    ///    - Creates nonce streams with verifiers (get pub nonces for each transaction)
    ///    - Opens deposit signing streams with verifiers (sends aggnonces for each transaction, receives partial sigs)
    ///    - Opens deposit finalization streams with verifiers (sends final signatures, receives movetx signatures)
    /// 3. Collects signatures from operators
    /// 4. Waits for all tasks to complete
    /// 5. Returns signed move transaction
    ///
    /// The following pipelines are used to coordinate the signing process, these move the data between the verifiers and the aggregator:
    ///    - Nonce aggregation
    ///    - Nonce distribution
    ///    - Signature aggregation
```

**File:** core/src/servers.rs (L106-139)
```rust
            let tls_config = if config.client_verification {
                ServerTlsConfig::new()
                    .identity(server_identity)
                    .client_ca_root(client_ca)
            } else {
                ServerTlsConfig::new().identity(server_identity)
            };

            let service = InterceptedService::new(
                service,
                if config.client_verification {
                    let client_cert = CertificateDer::from_pem_file(&config.client_cert_path)
                        .wrap_err(format!(
                            "Failed to read client certificate from {}",
                            config.client_cert_path.display()
                        ))?
                        .to_owned();

                    let aggregator_cert =
                        CertificateDer::from_pem_file(&config.aggregator_cert_path)
                            .wrap_err(format!(
                                "Failed to read aggregator certificate from {}",
                                config.aggregator_cert_path.display()
                            ))?
                            .to_owned();

                    OnlyAggregatorAndSelf {
                        aggregator_cert,
                        our_cert: client_cert,
                    }
                } else {
                    Noop
                },
            );
```

**File:** core/src/builder/transaction/mod.rs (L306-343)
```rust
pub fn create_move_to_vault_txhandler(
    deposit_data: &mut DepositData,
    paramset: &'static ProtocolParamset,
) -> Result<TxHandler<Unsigned>, BridgeError> {
    let nofn_xonly_pk = deposit_data.get_nofn_xonly_pk()?;
    let deposit_outpoint = deposit_data.get_deposit_outpoint();
    let nofn_script = Arc::new(CheckSig::new(nofn_xonly_pk));
    let security_council_script = Arc::new(Multisig::from_security_council(
        deposit_data.security_council.clone(),
    ));

    let deposit_scripts = deposit_data.get_deposit_scripts(paramset)?;

    Ok(TxHandlerBuilder::new(TransactionType::MoveToVault)
        .with_version(NON_STANDARD_V3)
        .add_input(
            NormalSignatureKind::NotStored,
            SpendableTxIn::from_scripts(
                deposit_outpoint,
                paramset.bridge_amount,
                deposit_scripts,
                None,
                paramset.network,
            ),
            SpendPath::ScriptSpend(0),
            DEFAULT_SEQUENCE,
        )
        .add_output(UnspentTxOut::from_scripts(
            paramset.bridge_amount,
            vec![nofn_script, security_council_script],
            None,
            paramset.network,
        ))
        // always use 0 sat anchor for move_tx, this will keep the amount in move to vault tx exactly the bridge amount
        .add_output(UnspentTxOut::from_partial(anchor_output(Amount::from_sat(
            0,
        ))))
        .finalize())
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

**File:** core/src/deposit.rs (L189-204)
```rust
                let recovery_script_pubkey = original_deposit_data
                    .recovery_taproot_address
                    .clone()
                    .assume_checked()
                    .script_pubkey();

                let recovery_extracted_xonly_pk = recovery_script_pubkey
                    .try_get_taproot_pk()
                    .wrap_err("Recovery taproot address is not a valid taproot address")?;

                let script_timelock = Arc::new(TimelockScript::new(
                    Some(recovery_extracted_xonly_pk),
                    paramset.user_takes_after,
                ));

                Ok(vec![deposit_script, script_timelock])
```
