### Title
Aggregator/Citrea ECDSA verification signature EIP-712 domain omits `chainId`/`verifyingContract`, allowing cross-deployment replay - (File: core/src/rpc/ecdsa_verification_sig.rs)

### Summary
The `withdraw`/`optimistic_payout` flows authenticate that a withdrawal or optimistic-payout request truly originates from Citrea/the aggregator by recovering an address from an EIP-712 signature and comparing it to `aggregator_verification_address` in config. The EIP-712 domain used for this signature is hard-coded with only a `name`/`version` pair and does not bind the signature to a specific chain or bridge instance, so a signature approving a withdrawal on one Clementine deployment (e.g. a Citrea testnet instance) is also valid on any other deployment that reuses the same aggregator verification key (e.g. a Citrea mainnet instance, or a redeployed instance), directly mirroring the reported "withdraw message replayable on a different chain" bug class.

### Finding Description
The verification signature domain is defined as: [1](#0-0) 
which only sets `name: "ClementineVerification"` and `version: "1"`, omitting `chainId` and `verifyingContract` from the EIP-712 domain separator.

This domain is used both to sign (in tests/tooling) and to verify the withdrawal authorization signature: [2](#0-1) 

The recovered address is compared against `aggregator_verification_address` from `BridgeConfig` at two enforcement points — the operator's `withdraw` RPC and the verifier's `sign_optimistic_payout`: [3](#0-2) [4](#0-3) 

Because the signed message (`OptimisticPayoutMessage`/`OperatorWithdrawalMessage`) only commits to `withdrawal_id`, `input_signature`, `input_outpoint`, `output_script_pubkey`, and `output_amount` — and the EIP-712 domain adds no `chainId`/`verifyingContract` binding — the signature carries no information tying it to a particular Citrea chain (Clementine's config explicitly models Citrea chains as distinct via `citrea_chain_id`, confirming multiple chain deployments are a first-class concept) or to a specific bridge/operator/verifier instance. If the same `aggregator_verification_secret_key`/address is configured for more than one deployment (e.g., staging vs. production, or a re-deployed instance sharing the aggregator key), a signature obtained for a withdrawal on one deployment is a valid, verbatim-replayable authorization on any other deployment whose operator/verifier set also trusts that same `aggregator_verification_address`, exactly analogous to the reported cross-chain replay of the withdraw message digest.

### Impact Explanation
If replayed, this breaks the authentication binding "verification signature signed by the trusted aggregator for deployment A" == "authorization valid only on deployment A." An attacker who has observed a valid verification signature on one deployment can present it to `withdraw`/`optimistic_payout_sign` on a different deployment that reuses the same verification key, causing operators/verifiers there to treat an unauthenticated request as aggregator-approved — an unauthenticated state-changing/broadcasting call category (operator will construct and broadcast a signed payout transaction, verifiers will contribute partial signatures) that can lead to funds moving to a script pubkey the recipient controls without a genuine per-deployment authorization from that deployment's aggregator.

### Likelihood Explanation
This requires the operational condition that the aggregator verification key/address is reused across two or more live Clementine/Citrea deployments — a realistic operational scenario during staging/production parallel operation, or a fork/redeploy retaining the same key. Given that, no privileged role is needed to exploit it: the attacker merely needs to have observed one legitimate verification signature and can then replay it verbatim against a different deployment's operator/verifier endpoints, both of which are externally-reachable gRPC methods.

### Recommendation
Add `chainId` (Citrea chain id, already tracked in `BridgeConfig::citrea_chain_id`) and/or `verifyingContract`/a deployment-specific salt to `CLEMENTINE_EIP712_DOMAIN`, or otherwise fold a unique deployment/network identifier into the `OptimisticPayoutMessage`/`OperatorWithdrawalMessage` structs, so signatures are cryptographically bound to a single deployment and cannot be replayed across chains/instances.

### Proof of Concept
1. Deploy two Clementine instances (A and B) that are configured with the same `aggregator_verification_address`/`aggregator_verification_secret_key` (e.g., staging and production sharing an aggregator key, or B being a fresh redeploy of A's aggregator identity).
2. Citrea/aggregator signs a legitimate `OperatorWithdrawalMessage` EIP-712 signature authorizing a withdrawal on instance A, per `sign_withdrawal_verification_signature`: [5](#0-4) 
3. An attacker who observes this signature (submitted in `WithdrawParamsWithSig.verification_signature` over the wire, per the proto) replays the identical `WithdrawParamsWithSig` payload against instance B's operator `withdraw` RPC.
4. `recover_address_from_ecdsa_signature::<OperatorWithdrawalMessage>` on instance B recomputes the same EIP-712 hash (domain has no chain/contract binding) and recovers the same `aggregator_verification_address`, passing the check at `core/src/rpc/operator.rs` lines 220-234, causing instance B to sign/broadcast a payout it never independently authorized.

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
