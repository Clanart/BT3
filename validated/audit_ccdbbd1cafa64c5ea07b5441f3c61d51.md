### Title
EIP-712 Domain Separator Missing `chain_id` and `verifying_contract` Allows Cross-Network/Cross-Deployment Replay of Withdrawal Authorization Signatures - (File: `core/src/rpc/ecdsa_verification_sig.rs`)

---

### Summary

The `CLEMENTINE_EIP712_DOMAIN` used to sign and verify withdrawal authorization signatures (`OptimisticPayoutMessage` and `OperatorWithdrawalMessage`) omits both `chain_id` and `verifying_contract` from the EIP-712 domain separator. A valid aggregator signature produced for one Citrea network or bridge deployment is cryptographically valid on any other deployment that shares the same aggregator key, bypassing the aggregator authorization guard on the withdrawal and optimistic-payout paths.

---

### Finding Description

In `core/src/rpc/ecdsa_verification_sig.rs`, the EIP-712 domain is defined as:

```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
``` [1](#0-0) 

The domain contains only `name` and `version`. It does not include:
- `chain_id` — the Citrea EVM chain ID, already present in `BridgeConfig` as `citrea_chain_id`
- `verifying_contract` — the bridge contract address, already present in `BridgeConfig` as `bridge_contract_address` [2](#0-1) 

This domain is used to compute the signing hash for both `OptimisticPayoutMessage` and `OperatorWithdrawalMessage`:

```rust
let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);
let address = signature.recover_address_from_prehash(&eip712_hash)...;
``` [3](#0-2) 

The recovered address is then compared against `aggregator_verification_address` in config to authorize the withdrawal. This check is enforced in both the operator's `withdraw` RPC handler and the verifier's `sign_optimistic_payout`: [4](#0-3) [5](#0-4) 

Because the domain separator does not bind to a specific chain or contract, a signature produced for withdrawal `(id=N, outpoint=O, amount=A, script=S)` on Citrea testnet4 is byte-for-byte identical to the signature that would be accepted for the same parameters on Citrea mainnet or any other deployment sharing the same aggregator key.

---

### Impact Explanation

The `verification_signature` is the only programmatic gate that enforces the aggregator's explicit authorization of a withdrawal request. If it can be replayed across deployments:

1. **Cross-network replay**: A signature authorized for a testnet4 withdrawal can be submitted to a mainnet operator/verifier running the same aggregator key, causing the operator to process a withdrawal that was never authorized for mainnet.
2. **Cross-deployment replay**: Two Clementine bridge instances on the same Bitcoin network (e.g., a staging and production deployment both connected to Bitcoin mainnet but different Citrea chain IDs) would accept each other's signatures.

In both cases, the aggregator authorization check — the only software control preventing unauthorized withdrawal processing — is bypassed. The downstream effect is that operators and verifiers sign and broadcast payout transactions for withdrawals the aggregator did not explicitly approve for that specific deployment, potentially draining bridge-controlled BTC UTXOs.

---

### Likelihood Explanation

- `aggregator_verification_address` is set by default in `BridgeConfig::default()` and in the example `.env`, meaning the guard is active in standard deployments.
- It is common operational practice to reuse the same aggregator key across testnet and mainnet deployments.
- An attacker only needs to observe a valid `verification_signature` from any public or semi-public deployment (e.g., testnet) and replay it against a mainnet deployment with matching withdrawal parameters.
- The Bitcoin `input_outpoint` constraint (UTXO must exist on the target network) limits cross-mainnet/testnet replay, but does not prevent cross-deployment replay on the same Bitcoin network.

---

### Recommendation

Include `chain_id` and `verifying_contract` in the EIP-712 domain, sourced from the runtime config:

```rust
// Constructed at startup from BridgeConfig
let domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
    chain_id: config.citrea_chain_id as u64,
    verifying_contract: config.bridge_contract_address.parse::<Address>()?,
};
```

This ensures that a signature produced for one Citrea chain ID and bridge contract address is cryptographically invalid on any other deployment, directly mirroring the fix recommended for the Airdrop.sol analog.

---

### Proof of Concept

1. Deploy two Clementine instances: **Instance A** (Citrea chain_id=5655, bridge=`0x3100...0002`) and **Instance B** (Citrea chain_id=9999, bridge=`0x3100...0002`), both using the same `aggregator_verification_address` key and both connected to Bitcoin regtest.

2. On Instance A, the aggregator signs a withdrawal authorization for:
   - `withdrawal_id = 0`
   - `input_outpoint = <some regtest UTXO>`
   - `output_amount = 999900000 sat`
   - `output_script_pubkey = <attacker's address>`

   The EIP-712 hash is computed over `CLEMENTINE_EIP712_DOMAIN` (name+version only), producing signature `σ`.

3. Submit the same withdrawal params plus `σ` to Instance B's `withdraw` or `optimistic_payout_sign` RPC. Instance B calls `recover_address_from_ecdsa_signature` with the same static domain, recovers the same address, and accepts `σ` as valid — even though the aggregator never authorized this withdrawal on Instance B.

4. Instance B's operator broadcasts the payout transaction, spending the bridge-controlled UTXO on the shared Bitcoin regtest network. [1](#0-0) [6](#0-5)

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

**File:** core/src/config/mod.rs (L85-90)
```rust
    /// Citrea's EVM Chain ID.
    pub citrea_chain_id: u32,
    /// Timeout in seconds for Citrea RPC calls.
    pub citrea_request_timeout: Option<Duration>,
    /// Bridge contract address.
    pub bridge_contract_address: String,
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

**File:** core/src/verifier.rs (L1601-1623)
```rust
        // if verification address is set in config, check if verification signature is valid
        if let Some(address_in_config) = self.config.aggregator_verification_address {
            // check if verification signature is provided by aggregator
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
            } else {
                // if verification signature is not provided, but verification address is set in config, return error
                return Err(BridgeError::ECDSAVerificationSignatureMissing);
            }
        }
```
