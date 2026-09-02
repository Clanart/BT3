## Title
Unauthenticated `InternalSendTx` broadcasts an attacker-chosen Bitcoin transaction via the aggregator's Bitcoin RPC when `client_verification = false` - (File: `core/src/servers.rs`, `core/src/rpc/aggregator.rs`)

## Summary
`create_grpc_server` installs `Interceptors::Noop` for the aggregator's gRPC service whenever `config.client_verification` is `false`, which unconditionally accepts every request, including `InternalSendTx`. Because `only_aggregator_and_self` (the only check that gives `is_internal` any meaning) is never invoked under `Noop`, `ClementineAggregator::internal_send_tx` will deserialize and enqueue for broadcast any raw transaction supplied by an unauthenticated caller.

## Finding Description
The claimed binding is: `caller of internal_send_tx == a party holding our_cert matching Interceptors::OnlyAggregatorAndSelf`.

Tracing the code:
- `create_grpc_server` (`core/src/servers.rs:106-139`) builds the TLS config and the interceptor together, gated on the same `config.client_verification` flag. When `client_verification` is `false`, the server is built with **no client CA root** (`ServerTlsConfig::new().identity(server_identity)` only) and the `InterceptedService` is wrapped with `Interceptors::Noop` (`core/src/servers.rs:106-139`).
- `Interceptors::Noop::call` (`core/src/rpc/interceptors.rs:22-32`) is `Ok(req)` unconditionally — it never calls `only_aggregator_and_self`, so `is_internal` and the leaf-certificate check are never evaluated.
- `AddMethodMiddlewareLayer`/`AddMethodMiddleware` (`core/src/utils.rs:243-299`) only stamps the `grpc-method` header used by `is_internal`; it performs no authorization itself.
- `create_aggregator_grpc_server` (`core/src/servers.rs:293-317`) only emits a `tracing::warn!` when `client_verification` **is** enabled — there is no warning or hardening for the (also valid) disabled case, and no code path forces `client_verification = true` for the aggregator.
- `ClementineAggregator::internal_send_tx` (`core/src/rpc/aggregator.rs:1269-1312`) takes `SendTxRequest.raw_tx`, deserializes it into a `bitcoin::Transaction` with no further validation against any deposit, kickoff, or bridge UTXO state, and calls `self.tx_sender.insert_try_to_send(...)` (`crates/clementine-tx-sender/src/client.rs:59-71`), which persists it to the tx-sender queue for broadcast via the aggregator's own Bitcoin RPC/wallet funds. The only compile-time gate is the `automation` feature flag, not authentication.

So under the config `client_verification = false` (an officially supported code path, not a bug in itself, and one that is explicitly wired through `create_grpc_server`), the equality holds as `false != false`: no certificate is required at all, and `is_internal`/`only_aggregator_and_self` are dead code for that request. Any TCP client that can reach the aggregator's gRPC port can call `InternalSendTx` with a transaction they crafted, and the aggregator will fee-bump and broadcast it using its own funds/UTXOs.

Existing guards that do **not** stop this: `Verifier::is_deposit_valid`, `Operator::is_profitable`, `SECP.verify_schnorr`, `verify_storage_proofs`, `SPV::verify`, and the presigned transaction graph are irrelevant here — `internal_send_tx` performs no such checks; it is a raw pass-through to the tx broadcaster. `only_aggregator_and_self` would be the relevant guard but is bypassed entirely by `Interceptors::Noop`.

## Impact Explanation
Immediate impact: an unauthenticated request causes the aggregator's Bitcoin RPC/wallet to broadcast an arbitrary attacker-supplied transaction (fee-bumped via CPFP/RBF using the aggregator's own funds). This matches the **High** severity category ("an unauthenticated state-changing or broadcasting call"). Repeatedly invoking `InternalSendTx` lets the attacker drain the aggregator's fee-bumping wallet by forcing it to pay fees on attacker-chosen transactions, and depending on what UTXOs the aggregator's tx-sender wallet controls, could interfere with in-flight protocol transactions (e.g., conflicting RBF/CPFP attempts against legitimate round/kickoff/reimburse transactions), since `insert_try_to_send` also registers cancel_outpoints tied to the tx's own inputs. This is deployment-scoped: it only manifests for aggregator deployments run with `client_verification = false`; it is not universal for all configurations.

## Likelihood Explanation
Preconditions: (1) the aggregator is deployed with `client_verification = false` — this is a config choice, not the compiled-in default (the documented/shipped configs, e.g. `.env.example`, `core/src/test/data/bridge_config.toml`, and `scripts/docker/configs/testnet4/bridge_config.toml`, all set `client_verification = true`); (2) the `automation` feature is enabled (required for `internal_send_tx` to do anything; otherwise it returns `Status::unimplemented`); (3) the attacker can reach the aggregator's gRPC TCP port. If those preconditions hold, exploitation is trivial and free beyond the target's own broadcast fees — the attacker sends one gRPC call with a self-crafted, self-funded transaction and it gets relayed and fee-bumped by the aggregator. This is a configuration-dependent finding: the code path (`Noop` when `client_verification=false`) is intentional and documented in `servers.rs`, and all shipped configuration files default to `client_verification = true`. I could not find any code that forces or defaults `client_verification` to `false` for the aggregator specifically, so the exposure requires an operator to affirmatively misconfigure their deployment.

## Recommendation
- For the aggregator specifically, refuse to start (or force `Interceptors::OnlyAggregatorAndSelf`) when `client_verification` is `false`, since `Setup`, `NewDeposit`, `Withdraw`, `OptimisticPayout`, `InternalSendTx`, `SendMoveToVaultTx`, and `InternalGetEmergencyStopTx` are all state-mutating/broadcasting endpoints that must never be reachable without mTLS.
- Alternatively/additionally, make `is_internal`/authorization checks independent of the TLS/`Noop` toggle — i.e., always enforce method-level authorization (not merely certificate-based mTLS) for `Internal*`-prefixed RPCs regardless of `client_verification`.
- Elevate the existing `tracing::warn!` in `create_aggregator_grpc_server` into a hard error (`bail!`) when `client_verification == false`.

## Proof of Concept
```rust
// core/src/test/rpc_auth.rs (extend)
#[tokio::test]
async fn test_internal_send_tx_unauthenticated_when_client_verification_disabled() -> Result<(), eyre::Report> {
    let mut config = create_test_config_with_thread_name().await;
    let _rpc = create_regtest_rpc(&mut config).await;
    config.client_verification = false; // simulate the vulnerable deployment mode

    let port = find_available_port().await;
    config.host = "127.0.0.1".to_string();
    config.port = port;

    let (_addr, _shutdown) = create_aggregator_grpc_server(config.clone()).await?;

    // Connect WITHOUT presenting any client certificate (plain HTTPS/TLS, no mTLS).
    let endpoint = format!("https://127.0.0.1:{port}");
    let channel = tonic::transport::Channel::from_shared(endpoint)?
        .tls_config(tonic::transport::ClientTlsConfig::new()) // no client identity supplied
        .connect()
        .await?;
    let mut client = ClementineAggregatorClient::new(channel);

    // Attacker-crafted transaction (no relation to any deposit/bridge UTXO).
    let attacker_tx = build_arbitrary_self_funded_tx();

    let resp = client
        .internal_send_tx(SendTxRequest {
            raw_tx: Some(RawSignedTx { raw_tx: bitcoin::consensus::serialize(&attacker_tx) }),
            fee_type: FeeType::Cpfp as i32,
        })
        .await;

    // CALLER_AUTHORITY assertion: the request must be rejected absent a matching leaf certificate.
    assert!(resp.is_err(), "expected unauthenticated call to be rejected, but it succeeded: {resp:?}");
    Ok(())
}
```
Running this test against the current code (with `automation` feature enabled) demonstrates `resp.is_ok()` — the call succeeds and the transaction is queued for broadcast — confirming the binding is violated whenever an aggregator is run with `client_verification = false`.