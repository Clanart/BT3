### Title
Unauthenticated `SendMoveToVaultTx` RPC lets any caller broadcast/tag an arbitrary move-to-vault transaction, desynchronizing the deposit-tracking binding - (File: `core/src/rpc/aggregator.rs`)

### Summary
The aggregator's `send_move_to_vault_tx` RPC is reachable without any client-certificate authentication, unlike verifier/operator RPCs which are gated by the `OnlyAggregatorAndSelf` mTLS interceptor. This RPC accepts a caller-supplied `deposit_outpoint` and `raw_tx`, verifies only that the transaction is *structurally* a movetx (input/output counts, output amounts, output script pubkeys), and then broadcasts it while tagging it in the DB/tx-sender with whatever `deposit_outpoint` the caller supplied - without checking that the transaction's actual input (`movetx.input[0].previous_output`) matches that `deposit_outpoint`.

### Finding Description
`new_deposit` performs the N-of-N MuSig2 signing flow and returns a fully signed movetx to the caller [1](#0-0) , but does not broadcast it. Broadcasting/tracking is done by a separate RPC, `send_move_to_vault_tx`, which takes the raw signed tx plus a `deposit_outpoint` parameter [2](#0-1) .

This RPC only performs structural checks on the transaction (input/output counts, output values, and output script pubkeys matching the vault/anchor scripts) [3](#0-2) . It never checks that `movetx.input[0].previous_output` equals the supplied `deposit_outpoint` before inserting the transaction into the tx-sender with that outpoint as metadata and broadcasting it: [4](#0-3) .

Crucially, per the project's own documentation, the aggregator gRPC server does not enforce client certificates on its endpoints - only verifiers/operators enforce that calls come from the aggregator: [5](#0-4) . This is corroborated by the interceptor logic which is only wired into verifier/operator servers via `OnlyAggregatorAndSelf`, and the aggregator server construction which contains no such interceptor: [6](#0-5) [7](#0-6) .

This is structurally identical to the reported bug class: two steps that are meant to stay in lock-step (`mint()` producing a value, `swap()` consuming it) are split into an authenticated/heavy step (`new_deposit`, which performs N-of-N signing) and a lightweight, unauthenticated step (`send_move_to_vault_tx`, which broadcasts and tags). Nothing binds the caller-supplied `deposit_outpoint` tag to the transaction actually being broadcast, so:
`{aggregator's tracked deposit_outpoint -> movetx}` ≠ `{the deposit_outpoint actually spent by that movetx}`.

An unauthenticated party who has observed (or itself legitimately produced, e.g. via their own `new_deposit` call) any validly N-of-N-signed movetx can call `send_move_to_vault_tx` and tag it under an arbitrary victim `deposit_outpoint`. This corrupts the aggregator's own bookkeeping (`TxMetadata.deposit_outpoint`, used by `insert_try_to_send` for tx-sender tracking/CPFP fee-bumping) for that deposit_outpoint, independently of what the legitimate `new_deposit`/`send_move_to_vault_tx` flow for that outpoint would later attempt to record.

### Impact Explanation
The RPC is an unauthenticated, state-changing (DB insert) and broadcasting call - matching the "High" impact category of an unauthenticated state-changing/broadcasting call directly. The lack of a binding check between the caller-supplied `deposit_outpoint` and the actual transaction input means the tx-sender's per-deposit tracking/CPFP metadata can be desynchronized from the transaction that will actually confirm for that deposit, mirroring the reported race between two supposedly-coupled timed operations.

### Likelihood Explanation
The RPC requires no credential beyond network reachability of the aggregator (confirmed unauthenticated by `docs/usage.md`), and the attacker only needs any structurally valid, previously/independently signed movetx (e.g., from their own legitimate deposit) to invoke it with a mismatched `deposit_outpoint`. No N-of-N key material or privileged role is needed to trigger the call.

### Recommendation
In `send_move_to_vault_tx`, verify that `movetx.input[0].previous_output` equals the caller-supplied `deposit_outpoint` before broadcasting/tagging, and require the aggregator to enforce authentication (e.g., extend mTLS/interceptor checks to the aggregator's own broadcasting/state-changing endpoints, or authenticate callers of `SendMoveToVaultTx`/`InternalSendTx` the same way verifier/operator internal RPCs are protected) so that only the entity that produced/owns a given deposit's move transaction can register/broadcast it under that deposit's outpoint.

### Proof of Concept
1. Attacker deposits BTC through the normal deposit flow and calls `new_deposit` to obtain a validly N-of-N-signed movetx for their own `deposit_outpoint_A` [1](#0-0) .
2. Attacker calls `send_move_to_vault_tx` (no authentication required, per `docs/usage.md` lines 192-203) supplying this valid `raw_tx` together with an unrelated victim's `deposit_outpoint_B` in the request [2](#0-1) .
3. The structural checks pass (correct input/output counts, output amounts, and vault/anchor script pubkeys) [3](#0-2) , since these checks never compare `movetx.input[0].previous_output` to `deposit_outpoint_B`.
4. The aggregator inserts a `TxMetadata` entry tagging `TransactionType::MoveToVault` for `deposit_outpoint_B` with the attacker's own txid, and broadcasts it [4](#0-3) , corrupting the tx-sender/DB tracking state for the victim's deposit outpoint independent of the legitimate movetx eventually produced for it.

### Citations

**File:** core/src/rpc/aggregator.rs (L1457-1458)
```rust
        timed_request(OVERALL_DEPOSIT_TIMEOUT, "Overall new deposit", async {
            let deposit_info: DepositInfo = request.into_inner().try_into()?;
```

**File:** core/src/rpc/aggregator.rs (L1973-2011)
```rust
    #[tracing::instrument(skip(self), err(level = tracing::Level::ERROR))]
    async fn send_move_to_vault_tx(
        &self,
        request: Request<clementine::SendMoveTxRequest>,
    ) -> Result<Response<clementine::Txid>, Status> {
        tracing::info!("Send move to vault tx rpc called");
        #[cfg(not(feature = "automation"))]
        {
            let _ = request;
            return Err(Status::unimplemented(
                "Automation is disabled, cannot automatically send move to vault tx.",
            ));
        }

        #[cfg(feature = "automation")]
        {
            use bitcoin::Amount;
            use std::sync::Arc;

            use crate::builder::{
                address::create_taproot_address,
                script::{CheckSig, Multisig, SpendableScript},
                transaction::anchor_output,
            };

            let request = request.into_inner();
            let movetx: bitcoin::Transaction = bitcoin::consensus::deserialize(
                &request
                    .raw_tx
                    .ok_or_eyre("raw_tx is required")
                    .map_to_status()?
                    .raw_tx,
            )
            .wrap_err("Failed to deserialize movetx")
            .map_to_status()?;
            let deposit_outpoint: bitcoin::OutPoint = request
                .deposit_outpoint
                .ok_or(Status::invalid_argument("deposit_outpoint is required"))?
                .try_into()?;
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

**File:** core/src/rpc/aggregator.rs (L2075-2100)
```rust
            let mut dbtx = self.db.begin_transaction().await?;
            self.tx_sender
                .insert_try_to_send(
                    &mut dbtx,
                    Some(TxMetadata {
                        deposit_outpoint: Some(deposit_outpoint),
                        operator_xonly_pk: None,
                        round_idx: None,
                        kickoff_idx: None,
                        tx_type: TransactionType::MoveToVault,
                    }),
                    &movetx,
                    FeePayingType::CPFP,
                    None,
                    &[],
                    &[],
                    &[],
                    &[],
                )
                .await
                .map_to_status()?;
            dbtx.commit()
                .await
                .map_err(|e| Status::internal(format!("Failed to commit db transaction: {e}")))?;

            Ok(Response::new(movetx.compute_txid().into()))
```

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

**File:** core/src/rpc/interceptors.rs (L1-33)
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

fn is_internal(req: &Request<()>) -> bool {
    // This normally doesn't exist but we add it in the AddMethodMiddleware
    let Some(path) = req.metadata().get("grpc-method") else {
        // No grpc method? this should not happen
        tracing::error!("Missing grpc-method header in request");
        return false;
    };
    path.as_bytes().starts_with(b"Internal")
}

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
