### Title
Missing `chain_id` in EIP-712 Domain Allows Cross-Network Replay of Withdrawal Verification Signatures - (File: `core/src/rpc/ecdsa_verification_sig.rs`)

---

### Summary

The `CLEMENTINE_EIP712_DOMAIN` used to sign and verify withdrawal authorization signatures (`OptimisticPayoutMessage` and `OperatorWithdrawalMessage`) omits the `chain_id` field. Because the Citrea chain ID is known at runtime but excluded from the domain separator, a verification signature produced for one Citrea deployment is cryptographically valid on any other Citrea deployment that shares the same aggregator verification key, enabling cross-network replay of withdrawal authorizations.

---

### Finding Description

In `core/src/rpc/ecdsa_verification_sig.rs`, the EIP-712 domain is defined as:

```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
``` [1](#0-0) 

The EIP-712 standard explicitly provides a `chainId` field in the domain separator for the purpose of preventing cross-chain replay. Here, neither `chain_id` nor any network-specific value is included. The `citrea_chain_id` is a first-class field in `BridgeConfig` and is used for compatibility checks elsewhere, but it is never incorporated into the signing domain. [2](#0-1) 

The domain is used in two places:

1. **Operator withdrawal path** (`core/src/rpc/operator.rs`, `withdraw` RPC): `recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>` is called with `CLEMENTINE_EIP712_DOMAIN`. [3](#0-2) 

2. **Optimistic payout path** (`core/src/verifier.rs`, `sign_optimistic_payout`): `recover_address_from_ecdsa_signature::<OptimisticPayoutMessage>` is called with the same domain. [4](#0-3) 

The hash computed and verified is:

```rust
let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);
let address = signature.recover_address_from_prehash(&eip712_hash)...;
``` [5](#0-4) 

Because the domain is static and network-agnostic, the same `(withdrawal_id, input_signature, input_outpoint, output_script_pubkey, output_amount)` tuple signed on Citrea chain A produces a signature that passes verification on Citrea chain B, provided the same aggregator verification key is configured on both.

---

### Impact Explanation

The `verification_signature` is the sole authorization gate that prevents an unauthorized party from triggering a payout. If it can be replayed across networks, an operator on a second Citrea deployment (e.g., a parallel testnet or a newly launched mainnet sharing the same aggregator key) will accept a withdrawal that was only authorized for the first deployment. The operator then constructs and broadcasts a real Bitcoin payout transaction, spending BTC from the bridge vault for a withdrawal that was never legitimately authorized on that chain. This constitutes unauthorized spending of bridge-controlled BTC.

The `aggregator_verification_address` check is optional (guarded by `if let Some(...)`), so deployments that do configure it are the ones exposed. [6](#0-5) 

---

### Likelihood Explanation

The preconditions are:
1. The same aggregator verification key (`AGGREGATOR_VERIFICATION_ADDRESS`) is configured on two or more Citrea deployments (e.g., testnet4 chain_id=5655 and regtest chain_id=62298 — both are documented in the repo's config files).
2. A withdrawal with matching params (same `withdrawal_id`, same Bitcoin `input_outpoint`) exists on both chains — realistic when the same Bitcoin UTXO set is shared (e.g., two Citrea L2s anchored to the same Bitcoin testnet4), or during a network migration where UTXOs are replicated.

Both conditions are operationally plausible given the multi-network deployment configs present in the repository. [7](#0-6) [8](#0-7) 

---

### Recommendation

Include `citrea_chain_id` in the EIP-712 domain separator so that signatures are bound to a specific Citrea chain:

```rust
pub fn clementine_eip712_domain(chain_id: u64) -> Eip712Domain {
    alloy_sol_types::eip712_domain! {
        name: "ClementineVerification",
        version: "1",
        chain_id: chain_id,
    }
}
```

Pass `config.citrea_chain_id as u64` when constructing the domain in both `recover_address_from_ecdsa_signature` call sites (operator `withdraw` and verifier `sign_optimistic_payout`). The signing side (aggregator) must use the same chain-specific domain.

---

### Proof of Concept

1. Deploy two Citrea networks (chain A: `chain_id=5655`, chain B: `chain_id=62298`) both configured with the same `AGGREGATOR_VERIFICATION_ADDRESS`.
2. On chain A, the aggregator signs a `WithdrawParamsWithSig` for `withdrawal_id=0`, `input_outpoint=X:0`, `output_amount=N`. The resulting `verification_signature` is computed over `CLEMENTINE_EIP712_DOMAIN` (no chain_id).
3. Submit the identical `WithdrawParamsWithSig` (same params, same `verification_signature`) to an operator on chain B.
4. The operator calls `recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>` with the same static domain — the recovered address matches `address_in_config`, the check passes.
5. The operator proceeds to `self.operator.withdraw(...)`, constructs a payout transaction, and broadcasts it on Bitcoin, spending BTC that was never authorized for chain B. [9](#0-8)

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

**File:** core/src/config/mod.rs (L85-87)
```rust
    /// Citrea's EVM Chain ID.
    pub citrea_chain_id: u32,
    /// Timeout in seconds for Citrea RPC calls.
```

**File:** core/src/rpc/operator.rs (L209-239)
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

**File:** scripts/docker/configs/regtest/.env.regtest (L19-19)
```text
CITREA_CHAIN_ID=62298
```

**File:** scripts/docker/configs/testnet4/bridge_config.toml (L63-63)
```text
citrea_chain_id = 5655
```
