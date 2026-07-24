### Title
Missing `chain_id` and `verifying_contract` in EIP-712 Domain Enables Cross-Deployment Replay of Withdrawal Verification Signatures - (`core/src/rpc/ecdsa_verification_sig.rs`)

### Summary

The `CLEMENTINE_EIP712_DOMAIN` used to authenticate optimistic payout and operator withdrawal requests is defined with only `name` and `version`, omitting `chain_id` and `verifying_contract`. This produces an identical domain separator across every Clementine deployment and every Citrea chain ID, allowing a valid `verification_signature` obtained from one deployment to be replayed verbatim on any other deployment where the same withdrawal parameters are present.

### Finding Description

`CLEMENTINE_EIP712_DOMAIN` is declared as a static constant:

```rust
pub static CLEMENTINE_EIP712_DOMAIN: Eip712Domain = alloy_sol_types::eip712_domain! {
    name: "ClementineVerification",
    version: "1",
};
``` [1](#0-0) 

The EIP-712 specification requires `chainId` and `verifyingContract` in the domain separator to prevent cross-chain and cross-contract replay. Neither field is present here. The domain separator is therefore a fixed value, identical for every Clementine instance on every network.

This domain is used in `recover_address_from_ecdsa_signature` to verify the aggregator's `verification_signature` for both `OptimisticPayoutMessage` and `OperatorWithdrawalMessage`: [2](#0-1) 

The recovered address is then compared against `aggregator_verification_address` in the verifier's `sign_optimistic_payout`: [3](#0-2) 

and in the operator's `withdraw` RPC handler: [4](#0-3) 

The signed message covers `withdrawal_id`, `input_signature`, `input_outpoint_txid`, `input_outpoint_vout`, `output_script_pubkey`, and `output_amount`: [5](#0-4) 

None of these fields bind the signature to a specific Citrea chain ID or bridge contract address. The config already stores `citrea_chain_id` and `bridge_contract_address`: [6](#0-5) 

but neither is included in the signed domain or message.

**Exploit path:**

1. Clementine is deployed on Citrea testnet (chain ID A) and Citrea mainnet (chain ID B) with the same aggregator ECDSA key (`aggregator_verification_address` identical on both).
2. A legitimate withdrawal with `withdrawal_id = N`, `input_outpoint = X`, `output_script_pubkey = S`, `output_amount = V` is processed on testnet. The aggregator signs a `verification_signature` over these params using `CLEMENTINE_EIP712_DOMAIN`.
3. The same Bitcoin UTXO `X` is also a valid withdrawal UTXO on mainnet (e.g., during a migration or parallel deployment), and the same `withdrawal_id = N` exists on mainnet's Citrea state.
4. An attacker submits the testnet `verification_signature` to the mainnet aggregator's `optimistic_payout` RPC with the same params.
5. The mainnet verifiers call `sign_optimistic_payout`. The domain separator is identical, so `recover_address_from_ecdsa_signature` returns the same aggregator address, the check passes, and the verifiers produce partial MuSig2 signatures for the optimistic payout transaction.
6. The bridge UTXO on mainnet is spent to the attacker-controlled `output_script_pubkey`.

The same replay applies to the operator `withdraw` path, where the `OperatorWithdrawalMessage` uses the same domain-less separator.

### Impact Explanation

A successful replay causes the bridge's `MoveToVault` UTXO (holding the full bridge denomination, currently 10 BTC) to be spent via the optimistic payout path to an address the attacker controls. This is a direct, irreversible theft of bridged BTC from the vault. The operator's collateral and reimbursement flow are also affected because the payout is processed as legitimate.

### Likelihood Explanation

The preconditions are:
- Clementine deployed on more than one Citrea network with the same aggregator signing key (testnet + mainnet is the standard deployment pattern).
- A Bitcoin UTXO that is a valid withdrawal input on both deployments simultaneously (possible during migration, parallel testing, or if the same Bitcoin regtest/signet node is shared).
- The attacker can observe a `verification_signature` in transit (gRPC is mTLS-protected, but the aggregator itself is the signer and the signature is transmitted to verifiers and operators).

The combination is realistic for a protocol that explicitly tracks `citrea_chain_id` in its config and is designed for multi-network deployment.

### Recommendation

Include `chain_id` and `verifying_contract` in `CLEMENTINE_EIP712_DOMAIN`. Since the domain is currently a `static`, it must be constructed at runtime from the loaded config:

```rust
pub fn clementine_eip712_domain(chain_id: u64, verifying_contract: Address) -> Eip712Domain {
    alloy_sol_types::eip712_domain! {
        name: "ClementineVerification",
        version: "1",
        chain_id: chain_id,
        verifying_contract: verifying_contract,
    }
}
```

Pass `config.citrea_chain_id` and the parsed `config.bridge_contract_address` when constructing the domain in `recover_address_from_ecdsa_signature` and in the test helper `sign_withdrawal_verification_signature`. This binds every signature to a specific chain and contract, making cross-deployment replay cryptographically impossible.

### Proof of Concept

```
Deployment A (testnet, chain_id=5655, bridge=0xAAAA...):
  aggregator signs:
    eip712_hash(domain={name="ClementineVerification",version="1"},
                OptimisticPayoutMessage{withdrawal_id=0, input_outpoint=X, ...})
  → sig_A

Deployment B (mainnet, chain_id=1234, bridge=0xBBBB...):
  attacker submits sig_A with the same params to aggregator B's optimistic_payout RPC.
  
  recover_address_from_ecdsa_signature computes:
    eip712_hash(domain={name="ClementineVerification",version="1"},   ← SAME domain
                OptimisticPayoutMessage{withdrawal_id=0, input_outpoint=X, ...})
  → same hash → same recovered address → check passes → verifiers sign → BTC stolen
```

The domain separator is `keccak256(abi.encode(keccak256("EIP712Domain(string name,string version)"), keccak256("ClementineVerification"), keccak256("1")))` — a constant that never changes regardless of chain or contract. [1](#0-0) [3](#0-2) [4](#0-3) [6](#0-5)

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

**File:** core/src/config/mod.rs (L85-91)
```rust
    /// Citrea's EVM Chain ID.
    pub citrea_chain_id: u32,
    /// Timeout in seconds for Citrea RPC calls.
    pub citrea_request_timeout: Option<Duration>,
    /// Bridge contract address.
    pub bridge_contract_address: String,
    // Initial header chain proof receipt's file path.
```
