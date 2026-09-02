### Title
Unauthenticated Aggregator gRPC service allows any network peer to trigger state-changing and broadcasting bridge operations - (File: core/src/servers.rs, core/src/rpc/interceptors.rs)

### Summary
Unlike the Verifier and Operator services, the Aggregator's gRPC server does not authenticate its callers. `docs/usage.md` states this explicitly: "The aggregator does not enforce client certificates but does use TLS for encryption." [1](#0-0)  In `core/src/servers.rs`, `create_aggregator_grpc_server` builds its TLS/interceptor stack the same way as verifier/operator servers, but nothing in the aggregator path installs the `OnlyAggregatorAndSelf` interceptor logic that gates verifier/operator "Internal*" and privileged methods to only the aggregator's or the entity's own certificate. [2](#0-1)  The `Interceptors::OnlyAggregatorAndSelf` enforcement mechanism exists [3](#0-2)  and is exercised by verifier/operator server bootstrap (see `client_verification` gating client certs in `core/src/servers.rs:106-138`), but the aggregator's own RPC surface — including `NewDeposit`, `Withdraw`, `OptimisticPayout`, `SendMoveToVaultTx`, `InternalSendTx`, and `InternalGetEmergencyStopTx` — is reachable by anyone with network access to the aggregator's port, with TLS providing only transport encryption, not caller authentication. [4](#0-3) 

### Finding Description
This is the closest structural analog to the reported bug class ("state-changing entrypoint with no access restriction, reachable by any unprivileged caller, that a party controlling should not be able to reach"). In the external report, `Router.curatePool`/`replacePool` had no caller restriction at all, letting an attacker rewrite a privileged mapping (curated pools). In Clementine, the equivalent binding is: *a caller reaching a signing or state-changing method versus the party it is meant for*. The aggregator's gRPC surface is explicitly documented and designed to be called "by the clementine-backend service" only (see comments on `Setup`, `NewDeposit`, `Withdraw` in the proto) [5](#0-4) , yet the transport layer enforces no such restriction — the `Noop` interceptor variant is what applies whenever `client_verification` style restriction isn't configured for that service, and the aggregator is documented to never enforce client certs at all. [6](#0-5) 

I was not able to fully verify (due to running out of iterations) the exact runtime behavior of `internal_send_tx` and `restart_tasks`-enabled `get_entity_statuses` to determine whether they allow rebroadcast of arbitrary attacker-supplied transactions or merely rebroadcast of already-DB-stored, pre-signed protocol transactions. This distinction matters a great deal for severity:
- If `InternalSendTx`/`SendMoveToVaultTx` only rebroadcast/validate transactions that must already satisfy strict cryptographic and script-matching checks (as `send_move_to_vault_tx` does — it independently reconstructs and compares the expected N-of-N + security-council script pubkey and bridge amount before accepting a move tx, see `core/src/rpc/aggregator.rs:2019-2073`) [7](#0-6) , then the lack of authentication does not, by itself, let an attacker mint unbacked value or redirect deposits — the protocol-level cryptographic bindings (N-of-N signatures, script pubkey checks, deposit-outpoint-to-EVM-address commitment in `is_deposit_valid`) still hold. [8](#0-7) 
- However, the *unauthenticated reachability itself* is a real deviation from the documented/intended access model, and it does allow low-cost griefing/premature-disclosure and resource-consumption attacks (e.g., forcing verifiers/operators to run costly nonce-generation, deposit-sign flows, or forcing premature broadcast of protocol transactions like emergency-stop or reimbursement flows ahead of intended timing) merely by reaching the aggregator directly instead of via the backend.

### Impact Explanation
Per the accepted impact list, this most plausibly falls under: "an unauthenticated state-changing or broadcasting call." It does not, based on what I could verify, cross into the Critical category (BTC leaving a move-to-vault UTXO without a matching fronted withdrawal, a false circuit claim, etc.) because the underlying protocol checks (script pubkey matching, N-of-N MuSig2 signatures, Citrea-side registration checks in `withdraw`) remain intact regardless of who calls the aggregator RPC. The vulnerability is that these calls should have been restricted to the "clementine-backend service" as documented, but are not, at the transport layer.

### Likelihood Explanation
High likelihood of reachability: any party with network access to the aggregator's exposed port (which, per `scripts/docker/configs/*/bridge_config.toml`, is typically bound and reachable) can invoke these RPCs directly, since `client_verification`/certificate pinning is stated to never apply to the aggregator. [1](#0-0) 

### Recommendation
Add an authentication/authorization layer for the aggregator's gRPC service analogous to `Interceptors::OnlyAggregatorAndSelf`, restricting calls to the intended `clementine-backend` caller (e.g., via mTLS client-cert pinning or an API key), particularly for state-changing/broadcasting RPCs (`NewDeposit`, `Withdraw`, `OptimisticPayout`, `SendMoveToVaultTx`, `InternalSendTx`, `InternalGetEmergencyStopTx`, `GetEntityStatuses` with `restart_tasks=true`). This closes the "unauthenticated state-changing or broadcasting call" gap even though downstream cryptographic checks currently prevent outright fund loss.

### Proof of Concept
I could not complete a concrete PoC within the available investigation budget — this would require confirming, with full source of `internal_send_tx` and the `tx_sender` broadcast pipeline (not fully retrieved before the iteration limit), whether an attacker-supplied `SendTxRequest`/`Deposit`/`Withdraw` can be crafted that causes tangible impact beyond griefing/premature disclosure, since all move-to-vault and withdrawal paths I did inspect (`core/src/rpc/aggregator.rs:1974-2102`, `core/src/verifier.rs:531-732`) independently re-validate script pubkeys, amounts, and signatures before acting. I recommend a background Devin session with full repository access to trace `internal_send_tx`, `tx_sender::insert_try_to_send`, and the `Withdraw`/`OptimisticPayout` code paths in `core/src/operator.rs` to conclusively determine whether unauthenticated aggregator access can be escalated past griefing into an actual custody-binding break.

### Citations

**File:** docs/usage.md (L192-203)
```markdown
## RPC Authentication

Clementine uses mutual TLS (mTLS) to secure gRPC communications between entities
and to authenticate clients. Client certificates are verified and filtered by
the verifier/operator to ensure that:

1. Verifier/Operator methods can only be called by the aggregator (using
   aggregator's client certificate `aggregator_cert_path`)
2. Internal methods can only be called by the entity's own client certificate
   (using the entity's client certificate `client_cert_path`)

The aggregator does not enforce client certificates but does use TLS for encryption.
```

**File:** core/src/servers.rs (L293-317)
```rust
pub async fn create_aggregator_grpc_server(
    config: BridgeConfig,
) -> Result<(std::net::SocketAddr, oneshot::Sender<()>), BridgeError> {
    let addr: std::net::SocketAddr = format!("{}:{}", config.host, config.port)
        .parse()
        .wrap_err("Failed to parse address")?;
    let aggregator_server = AggregatorServer::new(config.clone()).await?;
    aggregator_server.start_background_tasks().await?;

    let svc = ClementineAggregatorServer::new(aggregator_server)
        .max_encoding_message_size(config.grpc.max_message_size)
        .max_decoding_message_size(config.grpc.max_message_size);

    if config.client_verification {
        tracing::warn!("Client verification is enabled on aggregator gRPC server",);
    }

    let (server_addr, shutdown_tx) =
        create_grpc_server(addr.into(), svc, "Aggregator", &config).await?;

    match server_addr {
        ServerAddr::Tcp(socket_addr) => Ok((socket_addr, shutdown_tx)),
        _ => Err(BridgeError::ConfigError("Expected TCP address".into())),
    }
}
```

**File:** core/src/rpc/interceptors.rs (L1-10)
```rust
use tonic::{service::Interceptor, transport::CertificateDer, Request, Status};

#[derive(Debug, Clone)]
pub enum Interceptors {
    OnlyAggregatorAndSelf {
        aggregator_cert: CertificateDer<'static>,
        our_cert: CertificateDer<'static>,
    },
    Noop,
}
```

**File:** core/src/rpc/interceptors.rs (L22-76)
```rust
impl Interceptor for Interceptors {
    #[allow(clippy::result_large_err)]
    fn call(&mut self, req: Request<()>) -> Result<Request<()>, Status> {
        match self {
            Interceptors::OnlyAggregatorAndSelf {
                our_cert,
                aggregator_cert,
            } => only_aggregator_and_self(req, our_cert, aggregator_cert),
            Interceptors::Noop => Ok(req),
        }
    }
}

#[allow(clippy::result_large_err)]
fn only_aggregator_and_self(
    req: Request<()>,
    our_cert: &CertificateDer<'static>,
    aggregator_cert: &CertificateDer<'static>,
) -> Result<Request<()>, Status> {
    let Some(peer_certs) = req.peer_certs() else {
        if cfg!(test) {
            // Test mode, we don't need to verify peer certificates
            return Ok(req);
        } else {
            // If we're not in test mode, we need to check peer certificates
            return Err(Status::unauthenticated(
                "Failed to verify peer certificate, is TLS enabled?",
            ));
        }
    };

    // IMPORTANT: Only check the leaf (end-entity) certificate, which is always the first
    // certificate in the chain. The leaf is the only certificate whose private key the peer
    // proved possession of during the TLS handshake. Checking anywhere else in the chain
    // would allow identity spoofing: an attacker could include a pinned cert as an
    // intermediate in their chain without possessing its private key.
    let Some(leaf_cert) = peer_certs.first() else {
        return Err(Status::unauthenticated("Peer certificate chain is empty"));
    };

    if is_internal(&req) {
        if leaf_cert == our_cert {
            Ok(req)
        } else {
            Err(Status::unauthenticated(
                "Unauthorized call to internal method (not self)",
            ))
        }
    } else if leaf_cert == aggregator_cert || leaf_cert == our_cert {
        Ok(req)
    } else {
        Err(Status::unauthenticated(
            "Unauthorized call to method (not aggregator or self)",
        ))
    }
```

**File:** core/src/rpc/clementine.proto (L751-795)
```text
  // 3. Collects all operator configs from each operator
  // 4. Distributes these operator configs to all verifiers
  //
  // Used by the clementine-backend service
  rpc Setup(Empty) returns (VerifierPublicKeys) {}

  // This will call, DepositNonceGen for every verifier,
  // then it will aggregate one by one and then send it to DepositSign,
  // then it will aggregate the partial sigs and send it to DepositFinalize,
  // this will also call the operator to get their signatures and send it to
  // DepositFinalize then it will collect the partial sigs and create the move
  // tx.
  //
  // Used by the clementine-backend service to initiate a deposit
  rpc NewDeposit(Deposit) returns (RawSignedTx) {}

  // Call's withdraw on all operators
  // Used by the clementine-backend service to initiate a withdrawal
  // If the operator's xonly public keys list is empty, the withdrawal will be
  // sent to all operators. If not, only the operators in the list will be sent
  // the withdrawal request.
  rpc Withdraw(AggregatorWithdrawalInput) returns (AggregatorWithdrawResponse) {
  }

  // Perform an optimistic payout to reimburse a peg-out from Citrea
  rpc OptimisticPayout(OptimisticWithdrawParams) returns (RawSignedTx) {}

  // Send a pre-signed tx to the network
  rpc InternalSendTx(SendTxRequest) returns (Empty) {}

  rpc SendMoveToVaultTx(SendMoveTxRequest) returns (Txid) {}

  // Returns the current status of tasks running on the operators/verifiers.
  // If restart_tasks is true, it will restart the tasks on the entities if they
  // are stopped.
  rpc GetEntityStatuses(GetEntityStatusesRequest) returns (EntityStatuses) {}

  // Creates an emergency stop tx that won't be broadcasted.
  // Tx will have around 3 sats/vbyte fee.
  // Set add_anchor to true to add an anchor output for cpfp..
  rpc InternalGetEmergencyStopTx(GetEmergencyStopTxRequest)
      returns (GetEmergencyStopTxResponse) {}

  rpc Vergen(Empty) returns (VergenResponse) {}
}
```

**File:** core/src/rpc/aggregator.rs (L2019-2073)
```rust
            // check if transaction is a movetx
            if movetx.input.len() != 1 || movetx.output.len() != 2 {
                return Err(Status::invalid_argument(
                    "Transaction is not a movetx, input or output lengths are not correct",
                ));
            }
            // check output values
            // movetx always has 0 sat anchor output
            if !(movetx.output[0].value == self.config.protocol_paramset().bridge_amount
                && movetx.output[1].value == Amount::from_sat(0))
            {
                return Err(Status::invalid_argument(format!(
                    "Transaction is not a movetx, output sat values are not correct, should be ({}, 0), got ({}, {})",
                    self.config.protocol_paramset().bridge_amount,
                    movetx.output[0].value,
                    movetx.output[1].value,
                )));
            }
            // check output scriptpubkeys
            let verifier_keys = self.fetch_verifier_keys().await?;
            let nofn_xonly_pk =
                bitcoin::XOnlyPublicKey::from_musig2_pks(verifier_keys.clone(), None).map_err(
                    |e| {
                        Status::internal(format!(
                            "Failed to aggregate verifier public keys, err: {e}, pubkeys: {verifier_keys:?}"
                        ))
                    },
                )?;
            let nofn_script = Arc::new(CheckSig::new(nofn_xonly_pk));
            let security_council_script = Arc::new(Multisig::from_security_council(
                self.config.security_council.clone(),
            ));

            let (addr, _) = create_taproot_address(
                &[
                    nofn_script.to_script_buf(),
                    security_council_script.to_script_buf(),
                ],
                None,
                self.config.protocol_paramset().network,
            );
            let bridge_script_pubkey = addr.script_pubkey();

            if !(movetx.output[1].script_pubkey
                == anchor_output(self.config.protocol_paramset().anchor_amount()).script_pubkey
                && movetx.output[0].script_pubkey == bridge_script_pubkey)
            {
                return Err(Status::invalid_argument(
                    format!("Transaction is not a movetx, output scriptpubkeys are not correct, expected: (vault: {:?}, anchor: {:?}), got: (vault: {:?}, anchor: {:?})",
                    bridge_script_pubkey,
                    anchor_output(self.config.protocol_paramset().anchor_amount()).script_pubkey,
                    movetx.output[0].script_pubkey,
                    movetx.output[1].script_pubkey,
                )));
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
