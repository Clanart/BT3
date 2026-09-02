### Title
Unauthenticated `InternalSendTx` broadcasts attacker-chosen transaction when `client_verification=false` - ([File: core/src/servers.rs], [File: core/src/rpc/interceptors.rs], [File: core/src/rpc/aggregator.rs])

### Summary
`ClementineAggregator::internal_send_tx` is only intended to be callable by the aggregator itself, enforced through the `is_internal`/`only_aggregator_and_self` check in the `Interceptors::OnlyAggregatorAndSelf` variant. `create_grpc_server` chooses that interceptor only when `config.client_verification` is `true`; when it is `false`, `Interceptors::Noop` is installed instead, which returns `Ok(req)` unconditionally, so the "internal only" binding is never checked for any caller.

### Finding Description
The intended binding is: `caller_identity == aggregator_self` for any RPC whose name starts with `Internal` (checked by `is_internal` in `core/src/rpc/interceptors.rs:12-20` and enforced in `only_aggregator_and_self` at lines 62-69). This equality is enforced **only** inside the `OnlyAggregatorAndSelf` interceptor branch.

`core/src/servers.rs:106-138` decides which interceptor to install:
```
let tls_config = if config.client_verification { ... client_ca_root ... } else { ServerTlsConfig::new().identity(server_identity) };
let service = InterceptedService::new(service, if config.client_verification { OnlyAggregatorAndSelf {...} } else { Noop });
```
When `client_verification=false`, `Interceptors::Noop::call` (`core/src/rpc/interceptors.rs:30`) simply returns `Ok(req)`, bypassing `is_internal` entirely — the equality check is never evaluated at all, i.e., it degenerates to "true for everyone."

`internal_send_tx` (`core/src/rpc/aggregator.rs:1269-1312`) takes the caller-supplied raw transaction, deserializes it, and unconditionally calls `self.tx_sender.insert_try_to_send(...)`, which persists it to the tx-sender queue for broadcast (`crates/clementine-tx-sender/src/client.rs:59-101`). There is no signature/ownership check on the transaction content inside `internal_send_tx` or `insert_try_to_send` — authorization was meant to come entirely from the interceptor layer.

With `client_verification=false`, any TCP client that connects to the aggregator's public gRPC port (no TLS client cert required) can call `InternalSendTx` and have an arbitrary transaction queued for network broadcast by the aggregator's own tx-sender/Bitcoin RPC infrastructure.

### Impact Explanation
The exploitable gap is real: it is an **unauthenticated state-changing/broadcasting call** — the aggregator will queue and attempt to broadcast whatever transaction the caller supplies, without checking `is_internal`/certificate identity at all in the `Noop` configuration.

However, the described Critical outcome ("attacker-chosen transaction broadcast... potentially spending a bridge-adjacent UTXO") does not follow automatically. Spending a UTXO controlled by the bridge (move-to-vault, emergency-stop, kickoff, etc.) requires a valid Musig2 N-of-N (or operator) Schnorr signature over that UTXO's script, which the attacker does not possess and cannot forge (`Verifier`/`Operator` key shares are out of scope per the threat model, and the tx-sender/Bitcoin consensus rules will reject a transaction whose witness does not satisfy the taproot script). `insert_try_to_send` only persists the tx to a DB queue; the actual on-chain broadcast still goes through Bitcoin's own script/signature validation, so an attacker-crafted transaction spending a bridge UTXO without a valid N-of-N signature cannot actually confirm or move bridge funds. Thus this does not, by itself, produce "BTC leaving a move-to-vault UTXO without a matching fronted withdrawal" or any of the listed Critical outcomes.

What is real and matches the rubric is the **High** category: "an unauthenticated state-changing or broadcasting call" — `InternalSendTx` accepts and queues arbitrary transactions from unauthenticated callers when `client_verification=false`, and this is repeatable for every call and applies to all `Internal*`-prefixed RPCs on the aggregator (e.g., `InternalGetEmergencyStopTx` similarly loses its intended restriction). This can be used for spam/DB pollution of the tx-sender queue and, if the attacker can supply a transaction that happens to be validly signed by some other means (out of scope here), for unauthorized broadcast — but under the stated attacker capabilities (no key shares), no actual bridge value can move.

### Likelihood Explanation
This requires the deployment precondition `client_verification=false`, which the prompt states is the "documented default," and requires the aggregator's gRPC port to be reachable by the attacker. Given that precondition, exploitation is trivial and free (no BTC cost) — a bare TCP connection with no certificate suffices to invoke `InternalSendTx`. It is fully repeatable across calls but does not scale into moving actual bridge funds because signature material is required and not available to the attacker.

### Recommendation
Do not gate `Internal*` methods solely by TLS interceptor configuration. Add an explicit, independent authorization check for `internal_send_tx` (and other `Internal*` RPCs) inside the handler itself — e.g., require the transaction to match a locally-known/expected pre-signed transaction (only accept transactions that the aggregator itself previously constructed/signed, verified by txid or signature check against known bridge pubkeys) rather than trusting arbitrary caller-supplied raw bytes. Additionally, make `client_verification=false` refuse to install `Noop` for `Internal*`-prefixed methods, or fail startup/log a hard warning distinguishing "public" vs "internal-only" method sets so the interceptor cannot silently become vacuous for privileged RPCs.

### Proof of Concept
```
// core/src/test/ (new test) — NOTE: exploring only the reachability, not fund movement.
#[tokio::test]
async fn test_unauthenticated_internal_send_tx_reaches_queue() {
    // 1. Build BridgeConfig with client_verification = false explicitly.
    // 2. Start aggregator gRPC server via create_aggregator_grpc_server(config).
    // 3. Connect a tonic Channel with NO client certificate/identity configured (plain TLS / or Noop path).
    // 4. Craft an arbitrary bitcoin::Transaction spending an attacker-chosen outpoint
    //    (e.g., a fake move-to-vault-shaped OutPoint) with an empty/garbage witness.
    // 5. Call aggregator_client.internal_send_tx(SendTxRequest { raw_tx: Some(tx.into()), fee_type: ... }).
    // 6. Assert: response is Ok(Empty) (i.e., the Noop interceptor did NOT reject the unauthenticated call,
    //    proving is_internal/only_aggregator_and_self was never evaluated).
    // 7. Query TxSenderDb::check_if_tx_exists_on_txsender for the txid and assert it now exists in the queue.
    // 8. Separately assert that, on regtest, `sendrawtransaction`/tx-sender's later broadcast attempt fails
    //    with a script/signature validation error (proving no bridge UTXO can actually be spent this way,
    //    which bounds the impact to "unauthenticated call accepted" rather than "funds moved").
}
```