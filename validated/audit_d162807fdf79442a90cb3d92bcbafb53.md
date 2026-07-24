### Title
Missing `chain_id` in EIP-712 Domain Separator Enables Cross-Deployment Replay of Aggregator Withdrawal Authorization Signatures — (`core/src/rpc/ecdsa_verification_sig.rs`)

---

### Summary

`CLEMENTINE_EIP712_DOMAIN` is constructed with only `name` and `version`, omitting both `chain_id` and `verifying_contract`. Any aggregator signature over `OptimisticPayoutMessage` or `OperatorWithdrawalMessage` produced for one Clementine deployment (e.g., Citrea testnet, chain_id 5655) is cryptographically identical to one produced for any other deployment sharing the same aggregator key, because the domain hash does not bind the signature to a specific chain. An attacker who captures a valid aggregator signature from one deployment can replay it verbatim on another deployment where the same `{withdrawal_id, input_outpoint, output_script_pubkey, output_amount}` tuple is valid, causing the operator or verifier to execute an unauthorized BTC payout from the bridge vault.

---

### Finding Description

`CLEMENTINE_EIP712_DOMAIN` is defined as:

```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
``` [1](#0-0) 

No `chain_id` and no `verifying_contract` field are present. Per EIP-712, omitting `chain_id` from the domain separator means the domain hash — and therefore every signed message hash — is identical across all EVM chains. The domain is used in two places:

1. **Verifier `sign_optimistic_payout`**: recovers the aggregator address from the signature over `OptimisticPayoutMessage` and compares it to `aggregator_verification_address` in config. [2](#0-1) 

2. **Operator `withdraw` RPC**: recovers the aggregator address from the signature over `OperatorWithdrawalMessage` and compares it to `aggregator_verification_address` in config. [3](#0-2) 

The recovery function computes:

```rust
let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);
let address = signature.recover_address_from_prehash(&eip712_hash)...;
``` [4](#0-3) 

Because `CLEMENTINE_EIP712_DOMAIN` contains no `chain_id`, the hash is the same on every EVM chain. A signature produced by the aggregator for a withdrawal on Citrea testnet (chain_id 5655, as configured in `.env.example`) produces an identical hash on Citrea mainnet or any other Citrea deployment. [5](#0-4) 

---

### Impact Explanation

If the same aggregator key is used across two Clementine deployments (testnet and mainnet, or two parallel mainnet deployments), an attacker who observes a valid aggregator-signed `OptimisticPayoutMessage` or `OperatorWithdrawalMessage` from deployment A can submit it unchanged to deployment B. If the same `withdrawal_id` maps to the same Bitcoin `input_outpoint` on deployment B (which is possible when the same Bitcoin UTXO is registered as a withdrawal UTXO on both chains, or when an attacker engineers this condition), the verifier/operator on deployment B will accept the signature as legitimate and execute the BTC payout from the bridge vault without a fresh authorization from the aggregator. The result is an unauthorized spend of bridged BTC or operator collateral.

The `aggregator_verification_address` guard is also **optional** — when not configured, the check is skipped entirely, removing even the partial protection: [6](#0-5) 

---

### Likelihood Explanation

Clementine is explicitly designed to run on multiple Bitcoin networks (mainnet, testnet4, signet, regtest) and multiple Citrea deployments. The same aggregator key being reused across deployments is a realistic operational assumption. The `withdrawal_id` is a sequential Citrea index; the `input_outpoint` is a Bitcoin UTXO. An attacker who can observe testnet traffic and identify a testnet withdrawal whose parameters coincide with a mainnet withdrawal (or who can engineer such a coincidence by making deposits on both chains) can execute the replay. The missing `chain_id` is a structural gap, not a configuration mistake.

---

### Recommendation

Add `chain_id` (and optionally `verifying_contract`) to `CLEMENTINE_EIP712_DOMAIN`. The `chain_id` should be read from the live Citrea chain at runtime (via the provider) or passed in from the verified `CITREA_CHAIN_ID` configuration, rather than hardcoded, to prevent cross-deployment replay:

```rust
pub fn clementine_eip712_domain(chain_id: u64) -> Eip712Domain {
    alloy_sol_types::eip712_domain! {
        name: "ClementineVerification",
        version: "1",
        chain_id: chain_id,
    }
}
```

Pass the domain into `recover_address_from_ecdsa_signature` instead of using the static global. The `chain_id` value should be sourced from the same `CITREA_CHAIN_ID` already present in configuration, ensuring the domain is bound to the specific Citrea deployment.

---

### Proof of Concept

1. Aggregator signs an `OptimisticPayoutMessage` for withdrawal_id=0, input_outpoint=`<UTXO_X>`, output_amount=1 BTC on Citrea testnet (chain_id=5655). The EIP-712 hash is computed over domain `{name:"ClementineVerification", version:"1"}` — no chain_id.
2. The same Bitcoin UTXO `<UTXO_X>` is registered as the withdrawal UTXO for withdrawal_id=0 on Citrea mainnet (different chain_id).
3. Attacker submits the identical signature bytes to the mainnet verifier's `OptimisticPayoutSign` RPC.
4. `recover_address_from_ecdsa_signature::<OptimisticPayoutMessage>` computes the same EIP-712 hash (domain is chain-agnostic), recovers the same aggregator address, and the check at `address_from_sig != address_in_config` passes.
5. The verifier signs the optimistic payout partial signature; the aggregator assembles the final musig2 signature and broadcasts the payout transaction, spending BTC from the mainnet bridge vault without a fresh mainnet authorization. [1](#0-0) [7](#0-6) [8](#0-7)

### Citations

**File:** core/src/rpc/ecdsa_verification_sig.rs (L42-45)
```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
```

**File:** core/src/rpc/ecdsa_verification_sig.rs (L125-129)
```rust
    let eip712_hash = params.eip712_signing_hash(&CLEMENTINE_EIP712_DOMAIN);

    let address = signature
        .recover_address_from_prehash(&eip712_hash)
        .wrap_err("Invalid signature")?;
```

**File:** core/src/verifier.rs (L1601-1617)
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

**File:** .env.example (L87-87)
```text
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
