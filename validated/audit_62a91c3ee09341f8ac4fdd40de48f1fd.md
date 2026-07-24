### Title
EIP-712 Domain Missing `chain_id` Allows Cross-Network Replay of Withdrawal Verification Signatures — (`File: core/src/rpc/ecdsa_verification_sig.rs`)

### Summary

The `CLEMENTINE_EIP712_DOMAIN` used to authenticate both operator withdrawal and optimistic payout requests omits `chain_id` and `verifying_contract`. A verification signature produced by the aggregator for a withdrawal on one Citrea network (e.g., testnet, `chain_id=5655`) is cryptographically identical to a valid signature on any other Citrea deployment that shares the same aggregator key, enabling cross-network replay that causes operators to pay out BTC without legitimate authorization.

### Finding Description

`core/src/rpc/ecdsa_verification_sig.rs` defines the EIP-712 domain used for all withdrawal authorization:

```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
``` [1](#0-0) 

The domain contains only `name` and `version`. EIP-712 explicitly defines `chainId` and `verifyingContract` as domain separator fields to prevent cross-chain and cross-contract replay. Both are absent here.

This domain is used to compute the signing hash for two message types:

- `OptimisticPayoutMessage` — signed by the aggregator to authorize optimistic payouts
- `OperatorWithdrawalMessage` — signed by the aggregator to authorize operator-level withdrawals [2](#0-1) 

The hash is computed and the signer address recovered in `recover_address_from_ecdsa_signature`, which is called in both the operator `withdraw` RPC and the verifier `sign_optimistic_payout` path: [3](#0-2) 

The recovered address is compared against `aggregator_verification_address` from config: [4](#0-3) [5](#0-4) 

Meanwhile, `BridgeConfig` already carries `citrea_chain_id: u32` — the Citrea EVM chain ID — which is configured per deployment but is never fed into the EIP-712 domain: [6](#0-5) 

### Impact Explanation

An aggregator verification signature for withdrawal_id=N, input_outpoint=UTXO_X, output_script_pubkey=P, output_amount=A on Citrea testnet (`chain_id=5655`) is byte-for-byte identical to a valid signature for the same parameters on Citrea mainnet (different `chain_id`). If the same aggregator key (`aggregator_verification_address`) is used across deployments — the standard operational pattern — an attacker can:

1. Obtain a legitimately-issued signature from the testnet/staging deployment.
2. Submit it to the mainnet operator's `withdraw` or `optimistic_payout` RPC with matching withdrawal parameters.
3. The operator accepts the signature (address recovered matches `aggregator_verification_address`), constructs and broadcasts the payout transaction, and loses BTC without a corresponding authorized withdrawal on mainnet.

This directly causes unauthorized loss of operator-controlled BTC and breaks the authorization invariant of the withdrawal flow.

### Likelihood Explanation

The same aggregator key being reused across Citrea testnet and mainnet is the expected operational pattern (the `.env.example` shows a single `AGGREGATOR_VERIFICATION_ADDRESS`). The `withdrawal_id` is a sequential integer starting from 0 on every deployment, so collisions in withdrawal parameters across networks are structurally likely. The attacker only needs to observe a valid signature from one network and replay it on another — no privileged access is required. [7](#0-6) 

### Recommendation

Include `chain_id` (and optionally `verifying_contract`) in `CLEMENTINE_EIP712_DOMAIN`. Since the domain is currently a `static`, it must be made runtime-configurable (constructed from `BridgeConfig.citrea_chain_id`) and passed into `recover_address_from_ecdsa_signature` and the signing path. For example:

```rust
pub fn make_eip712_domain(chain_id: u64) -> Eip712Domain {
    alloy_sol_types::eip712_domain! {
        name: "ClementineVerification",
        version: "1",
        chain_id: chain_id,
    }
}
```

The domain should be constructed once from `config.citrea_chain_id` and threaded through `Operator`, `Verifier`, and the test signing helper `sign_withdrawal_verification_signature`. [8](#0-7) 

### Proof of Concept

1. Deploy Clementine on Citrea testnet (`chain_id=T`) and mainnet (`chain_id=M`) with the same `aggregator_verification_address=ADDR`.
2. On testnet, register withdrawal: `withdrawal_id=0`, `input_outpoint=UTXO_X`, `output_script_pubkey=P`, `output_amount=A`.
3. Aggregator issues `verification_signature = ECDSA_sign(EIP712_hash({name:"ClementineVerification", version:"1"}, {withdrawal_id:0, ...}))` — domain has no chain_id, so the hash is identical on both networks.
4. On mainnet, the same `withdrawal_id=0` and `UTXO_X` exist (Bitcoin UTXO is chain-agnostic; sequential IDs collide).
5. Attacker calls mainnet operator's `withdraw` RPC with the testnet `verification_signature`.
6. `recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>(...)` recovers `ADDR` — check passes at line 232 of `core/src/rpc/operator.rs`.
7. Operator broadcasts payout transaction on mainnet Bitcoin, losing BTC without a valid mainnet authorization. [9](#0-8)

### Citations

**File:** core/src/rpc/ecdsa_verification_sig.rs (L20-40)
```rust
alloy_sol_types::sol! {
    #[derive(Debug)]
    struct OptimisticPayoutMessage {
        uint32 withdrawal_id;
        bytes input_signature;
        bytes32 input_outpoint_txid;
        uint32 input_outpoint_vout;
        bytes output_script_pubkey;
        uint64 output_amount;
    }

    #[derive(Debug)]
    struct OperatorWithdrawalMessage  {
        uint32 withdrawal_id;
        bytes input_signature;
        bytes32 input_outpoint_txid;
        uint32 input_outpoint_vout;
        bytes output_script_pubkey;
        uint64 output_amount;
    }
}
```

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

**File:** core/src/rpc/operator.rs (L220-234)
```rust
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
```

**File:** core/src/verifier.rs (L1604-1618)
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
                }
```

**File:** core/src/config/mod.rs (L85-87)
```rust
    /// Citrea's EVM Chain ID.
    pub citrea_chain_id: u32,
    /// Timeout in seconds for Citrea RPC calls.
```

**File:** .env.example (L45-45)
```text
AGGREGATOR_VERIFICATION_ADDRESS=0x242fbec93465ce42b3d7c0e1901824a2697193fd
```

**File:** core/src/test/sign.rs (L33-33)
```rust
    let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);
```
