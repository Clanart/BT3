### Title
Missing `chain_id` in EIP-712 Domain Separator Enables Cross-Deployment Replay of Aggregator Withdrawal Authorization Signatures — (`core/src/rpc/ecdsa_verification_sig.rs`)

---

### Summary

`CLEMENTINE_EIP712_DOMAIN` is constructed without a `chain_id` field. The config already carries `citrea_chain_id` (read from `CITREA_CHAIN_ID`, e.g. `5655` for regtest) but it is never bound into the domain separator. A valid aggregator signature produced on one Citrea deployment can therefore be replayed verbatim on any other Clementine deployment that shares the same aggregator key, bypassing the authorization gate that guards both the operator `Withdraw` RPC and the verifier `OptimisticPayoutSign` RPC.

---

### Finding Description

`CLEMENTINE_EIP712_DOMAIN` is defined as:

```rust
// core/src/rpc/ecdsa_verification_sig.rs  line 42-45
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
``` [1](#0-0) 

EIP-712 specifies that `chainId` must be included in the domain separator so that user-agents (and verifiers) can reject signatures produced for a different chain. The field is intentionally absent here; neither `chain_id` nor `verifying_contract` is bound.

The domain is consumed in `recover_address_from_ecdsa_signature`, which is called from two production paths:

1. **Operator `Withdraw` RPC** — `core/src/rpc/operator.rs` lines 220-233: recovers the signer address from `OperatorWithdrawalMessage` and compares it to `aggregator_verification_address`.
2. **Verifier `OptimisticPayoutSign` RPC** — `core/src/verifier.rs` lines 1604-1617: recovers the signer address from `OptimisticPayoutMessage` and compares it to `aggregator_verification_address`. [2](#0-1) [3](#0-2) 

The config already reads `CITREA_CHAIN_ID` into `BridgeConfig::citrea_chain_id` (line 214 of `core/src/config/env.rs`) and the `.env.example` shows `CITREA_CHAIN_ID=5655` alongside `AGGREGATOR_VERIFICATION_ADDRESS=0x242fbec93465ce42b3d7c0e1901824a2697193fd`, confirming both values are present at runtime — but `citrea_chain_id` is never passed to the domain constructor. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

When `aggregator_verification_address` is configured (the intended production posture), it is the **sole authorization gate** for the `Withdraw` and `OptimisticPayoutSign` RPCs. Bypassing it allows:

- **Operator `Withdraw`**: An attacker who holds a valid aggregator signature from any other Clementine deployment (e.g., testnet4 → mainnet, or staging → production) can submit it to the mainnet operator. If the `withdrawal_id`, `input_outpoint`, `output_script_pubkey`, and `output_amount` fields match a live withdrawal on the target deployment, the operator will construct and broadcast a payout transaction, draining the bridge vault UTXO for that deposit.
- **Verifier `OptimisticPayoutSign`**: The same replayed signature causes verifiers to emit a MuSig2 partial signature for an optimistic payout that the aggregator never approved for this deployment, enabling an unauthorized BTC payout.

The `input_outpoint` (a Bitcoin UTXO) is network-specific, which prevents naive cross-Bitcoin-network replay (testnet4 UTXOs do not exist on mainnet). However, replay is fully viable between two Clementine deployments that share the same Bitcoin network — for example, a staging and a production deployment both running against Bitcoin mainnet, or two successive deployments during a migration where the same deposit/withdrawal indices are reused. [6](#0-5) 

---

### Likelihood Explanation

- `AGGREGATOR_VERIFICATION_ADDRESS` is present in `.env.example` and is the documented production authorization mechanism.
- The `citrea_chain_id` field is already in the config, demonstrating that the developers are aware of the multi-network context; the omission from the domain is an oversight rather than a deliberate design choice.
- Clementine is expected to run on both testnet4 and mainnet simultaneously, and staging/production deployments sharing a Bitcoin network are a normal operational pattern.
- An attacker only needs to observe one valid aggregator signature (e.g., from a public testnet) and find a matching withdrawal on the target deployment.

---

### Recommendation

Bind `citrea_chain_id` (already present in `BridgeConfig`) into the domain separator at construction time, and make `CLEMENTINE_EIP712_DOMAIN` a function rather than a static:

```rust
// core/src/rpc/ecdsa_verification_sig.rs
pub fn clementine_eip712_domain(chain_id: u64) -> Eip712Domain {
    alloy_sol_types::eip712_domain! {
        name: "ClementineVerification",
        version: "1",
        chain_id: chain_id,
    }
}
```

Pass `config.citrea_chain_id as u64` wherever `CLEMENTINE_EIP712_DOMAIN` is currently referenced:
- `recover_address_from_ecdsa_signature` (add `chain_id: u64` parameter)
- `core/src/rpc/operator.rs` — `withdraw` handler
- `core/src/verifier.rs` — `sign_optimistic_payout`
- `core/src/test/sign.rs` — `sign_withdrawal_verification_signature` [7](#0-6) [8](#0-7) 

---

### Proof of Concept

1. Observe a valid aggregator signature `S` for withdrawal `W` (id=0, outpoint=`X:1`, amount=1 BTC) on Citrea testnet4 deployment A (chain_id=5655).
2. Deployment B (Citrea mainnet, chain_id=7000) has a deposit with the same `withdrawal_id=0` and a matching Bitcoin UTXO `X:1` (possible during a migration or if both deployments share a Bitcoin network).
3. Submit `S` to deployment B's operator `Withdraw` RPC with the same parameters.
4. `recover_address_from_ecdsa_signature` computes `eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN)` — the domain is identical on both deployments because `chain_id` is absent.
5. The recovered address matches `aggregator_verification_address`; the operator proceeds to construct and broadcast the payout transaction, releasing BTC from the bridge vault without the mainnet aggregator's approval. [1](#0-0) [9](#0-8)

### Citations

**File:** core/src/rpc/ecdsa_verification_sig.rs (L42-45)
```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
```

**File:** core/src/rpc/ecdsa_verification_sig.rs (L109-131)
```rust
pub fn recover_address_from_ecdsa_signature<M: WithdrawalMessage + alloy_sol_types::SolStruct>(
    deposit_id: u32,
    input_signature: taproot::Signature,
    input_outpoint: OutPoint,
    output_script_pubkey: ScriptBuf,
    output_amount: Amount,
    signature: PrimitiveSignature,
) -> Result<alloy::primitives::Address, BridgeError> {
    let params = M::new(
        deposit_id,
        input_signature,
        input_outpoint,
        output_script_pubkey,
        output_amount,
    );

    let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);

    let address = signature
        .recover_address_from_prehash(&eip712_hash)
        .wrap_err("Invalid signature")?;
    Ok(address)
}
```

**File:** core/src/rpc/operator.rs (L209-238)
```rust
        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.operator.config.aggregator_verification_address {
            let verification_signature = params
                .verification_signature
                .map(|sig| {
                    PrimitiveSignature::from_str(&sig).map_err(|e| {
                        Status::invalid_argument(format!("Invalid verification signature: {e}"))
                    })
                })
                .transpose()?;
            // check if verification signature is provided by aggregator
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>(
                        withdrawal_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature).map_to_status();
                }
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing).map_to_status();
            }
```

**File:** core/src/verifier.rs (L1604-1617)
```rust
            if let Some(verification_signature) = verification_signature {
                let address_from_sig =
                    recover_address_from_ecdsa_signature::<OptimisticPayoutMessage>(
                        deposit_id,
                        input_signature,
                        input_outpoint,
                        output_script_pubkey.clone(),
                        output_amount,
                        verification_signature,
                    )?;

                // check if verification signature is signed by the address in config
                if address_from_sig != address_in_config {
                    return Err(BridgeError::InvalidECDSAVerificationSignature);
```

**File:** core/src/config/env.rs (L214-214)
```rust
            citrea_chain_id: read_string_from_env_then_parse::<u32>("CITREA_CHAIN_ID")?,
```

**File:** .env.example (L45-87)
```text
AGGREGATOR_VERIFICATION_ADDRESS=0x242fbec93465ce42b3d7c0e1901824a2697193fd

READ_PARAMSET_FROM_ENV=1

NETWORK=regtest
NUM_ROUND_TXS=3
NUM_KICKOFFS_PER_ROUND=10
NUM_SIGNED_KICKOFFS=2
BRIDGE_AMOUNT=1000000000
KICKOFF_AMOUNT=0
OPERATOR_CHALLENGE_AMOUNT=200000000
COLLATERAL_FUNDING_AMOUNT=99000000
KICKOFF_BLOCKHASH_COMMIT_LENGTH=40
WATCHTOWER_CHALLENGE_BYTES=144
WINTERNITZ_LOG_D=4
USER_TAKES_AFTER=200
OPERATOR_CHALLENGE_TIMEOUT_TIMELOCK=144
OPERATOR_CHALLENGE_NACK_TIMELOCK=432
DISPROVE_TIMEOUT_TIMELOCK=720
ASSERT_TIMEOUT_TIMELOCK=576
OPERATOR_REIMBURSE_TIMELOCK=12
WATCHTOWER_CHALLENGE_TIMEOUT_TIMELOCK=288
TIME_TO_SEND_WATCHTOWER_CHALLENGE=216
LATEST_BLOCKHASH_TIMEOUT_TIMELOCK=360
FINALITY_DEPTH=1
START_HEIGHT=8148
GENESIS_HEIGHT=8148
GENESIS_CHAIN_STATE_HASH=1111111111111111111111111111111111111111111111111111111111111111
HEADER_CHAIN_PROOF_BATCH_SIZE=100
BRIDGE_NONSTANDARD=true

SERVER_CERT_PATH="certs/server/server.pem"
SERVER_KEY_PATH="certs/server/server.key"
CA_CERT_PATH="certs/ca/ca.pem"
CLIENT_CERT_PATH="certs/client/client.pem"
CLIENT_KEY_PATH="certs/client/client.key"
AGGREGATOR_CERT_PATH="certs/aggregator/aggregator.pem"
CLIENT_VERIFICATION=true
SECURITY_COUNCIL=1:50929b74c1a04954b78b4b6035e97a5e078a5a0f28ec96d547bfee9ace803ac0

CITREA_RPC_URL=http://127.0.0.1:1234
CITREA_LIGHT_CLIENT_PROVER_URL=http://127.0.0.1:1235
CITREA_CHAIN_ID=5655
```

**File:** core/src/config/mod.rs (L148-152)
```rust
    /// The ECDSA address of the citrea/aggregator that will sign the withdrawal params
    /// after manual verification of the optimistic payout and operator's withdrawal.
    /// Used for both an extra verification of aggregator's identity and to force citrea
    /// to check withdrawal params manually during some time after launch.
    pub aggregator_verification_address: Option<alloy::primitives::Address>,
```

**File:** core/src/test/sign.rs (L13-39)
```rust
pub fn sign_withdrawal_verification_signature<M: WithdrawalMessage + SolStruct>(
    config: &BridgeConfig,
    withdrawal_params: WithdrawParams,
) -> PrimitiveSignature {
    let signing_key = config
        .test_params
        .aggregator_verification_secret_key
        .clone()
        .unwrap();
    let (withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount) =
        crate::rpc::parser::operator::parse_withdrawal_sig_params(withdrawal_params).unwrap();

    let params = M::new(
        withdrawal_id,
        input_signature,
        input_outpoint,
        output_script_pubkey,
        output_amount,
    );

    let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);

    let signature = signing_key
        .sign_prehash_recoverable(eip712_hash.as_slice())
        .unwrap();

    PrimitiveSignature::from(signature)
```
