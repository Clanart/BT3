### Title
Insufficient Source-Chain Finality in MPC Prover Enables Double-Spend via Sequencer Reorg - (File: near/omni-prover/mpc-omni-prover/src/lib.rs)

### Summary
`MpcOmniProver` is hardcoded at initialization to accept bridge proofs at pre-final chain states: `EvmFinality::Latest` for `ChainKind::Abs` (Abstract ZK-rollup) and `StarknetFinality::AcceptedOnL2` for `ChainKind::Strk`. Both states are reversible before the corresponding L1 proof is submitted. If the source-chain transaction is reorganized after NEAR has already minted or released tokens, the user retains assets on both chains, breaking bridge collateralization.

### Finding Description

In `MpcOmniProver::init()`, the finality map is populated with two sub-final states:

```rust
finalities.insert(ChainKind::Abs, MpcFinality::Evm(EvmFinality::Latest));
finalities.insert(
    ChainKind::Strk,
    MpcFinality::Starknet(StarknetFinality::AcceptedOnL2),
);
``` [1](#0-0) 

`request_matches_finality` enforces that the submitted proof's finality level exactly matches the configured value, so only `Latest` / `AcceptedOnL2` proofs are accepted for these chains — stronger finality levels are rejected: [2](#0-1) 

When a proof passes, `verify_callback` extracts the event log and returns a `ProverResult` that the bridge contract uses to mint or release tokens: [3](#0-2) 

The bridge's `fin_transfer` then calls `verify_proof` and, on success, immediately mints/releases assets: [4](#0-3) 

**`EvmFinality::Latest` (Abstract):** Abstract is a ZK-rollup. `Latest` is the sequencer's most recent block, which has not yet had its ZK proof verified on Ethereum L1. The sequencer can reorganize transactions before L1 proof submission — this is a documented property of ZK-rollup soft finality.

**`StarknetFinality::AcceptedOnL2` (Starknet):** `AcceptedOnL2` means the Starknet sequencer has included the transaction, but the STARK proof has not yet been verified on Ethereum L1. `AcceptedOnL1` is the finalized state. The test suite itself confirms that `AcceptedOnL2` is intentionally distinct from and weaker than `AcceptedOnL1`: [5](#0-4) 

### Impact Explanation

If a source-chain transaction at `Latest`/`AcceptedOnL2` is reorganized after NEAR has already settled the corresponding `fin_transfer`:

1. The user's locked/burned tokens on Abstract or Starknet are restored by the reorg.
2. The minted/released tokens on NEAR remain with the user.
3. The bridge has issued tokens against a source-chain event that no longer exists, breaking collateralization and creating unbacked supply.

This maps to: **Balance/accounting corruption that breaks bridge collateralization**, and potentially **unauthorized mint of bridged assets**.

### Likelihood Explanation

ZK-rollup sequencers (both Abstract and Starknet) are centralized and can reorganize their pending state before L1 proof submission. Starknet sequencer reorgs before L1 proof are a known operational occurrence. An attacker who can time a transfer to coincide with a natural sequencer reorg, or who can influence sequencer ordering, can exploit this. The bridge's own nonce deduplication only prevents replay of the same proof — it does not protect against a reorganized source event that was already consumed.

### Recommendation

1. Change the configured finality for `ChainKind::Abs` from `EvmFinality::Latest` to `EvmFinality::Finalized` (or at minimum `EvmFinality::Safe`).
2. Change the configured finality for `ChainKind::Strk` from `StarknetFinality::AcceptedOnL2` to `StarknetFinality::AcceptedOnL1`.
3. If faster bridging is required for these chains, implement an explicit fast-transfer / advance-payment mechanism (as already exists for EVM→NEAR flows) rather than weakening the proof finality requirement at the prover level.

### Proof of Concept

1. User initiates `init_transfer` on Abstract (or Starknet), locking 1000 USDC.
2. Relayer immediately submits a `MpcVerifyProofArgs` with `EvmFinality::Latest` (or `StarknetFinality::AcceptedOnL2`) to `MpcOmniProver::verify_proof` on NEAR.
3. MPC network confirms the transaction exists at `Latest`/`AcceptedOnL2`; `verify_callback` returns `ProverResult::InitTransfer`.
4. NEAR bridge's `fin_transfer_callback` mints 1000 bridged USDC to the user on NEAR.
5. The Abstract/Starknet sequencer reorganizes the block containing the user's `init_transfer` (natural reorg or sequencer-level rollback before L1 proof).
6. The user's 1000 USDC on Abstract/Starknet is restored.
7. The user now holds 1000 USDC on the source chain **and** 1000 bridged USDC on NEAR — unbacked supply created. [1](#0-0) [6](#0-5)

### Citations

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L55-67)
```rust
    pub fn init(mpc_contract_id: AccountId) -> Self {
        let mut finalities = HashMap::new();
        finalities.insert(ChainKind::Abs, MpcFinality::Evm(EvmFinality::Latest));
        finalities.insert(
            ChainKind::Strk,
            MpcFinality::Starknet(StarknetFinality::AcceptedOnL2),
        );

        Self {
            mpc_contract_id,
            finalities,
        }
    }
```

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L95-115)
```rust
        require!(
            Self::request_matches_finality(&payload_v1.request, finality),
            ProverError::FinalityMismatch.as_ref()
        );

        let request_args = VerifyForeignTransactionRequestArgs {
            request: payload_v1.request.clone(),
            domain_id: DomainId(FOREIGN_TX_DOMAIN_ID),
            payload_version: ForeignTxPayloadVersion::V1,
        };

        ext_mpc_contract::ext(self.mpc_contract_id.clone())
            .with_static_gas(VERIFY_FOREIGN_TX_GAS)
            .with_attached_deposit(ONE_YOCTO)
            .verify_foreign_transaction(request_args)
            .then(
                Self::ext(near_sdk::env::current_account_id())
                    .with_static_gas(VERIFY_CALLBACK_GAS)
                    .verify_callback(args.proof_kind, args.sign_payload, chain_kind),
            )
    }
```

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L130-152)
```rust
    ) -> Result<ProverResult, String> {
        let mpc_response = call_result.map_err(|_| ProverError::InvalidProof.to_string())?;

        let sign_payload = ForeignTxSignPayload::try_from_slice(&sign_payload_bytes)
            .map_err(|_| ProverError::ParseArgs.to_string())?;

        let expected_hash = sign_payload
            .compute_msg_hash()
            .map_err(|_| ProverError::InvalidPayloadHash.to_string())?;

        if expected_hash != mpc_response.payload_hash {
            return Err(ProverError::InvalidPayloadHash.to_string());
        }

        let ForeignTxSignPayload::V1(ref payload_v1) = sign_payload;

        if chain_kind == ChainKind::Strk {
            Self::parse_starknet_result(proof_kind, chain_kind, payload_v1)
        } else {
            let log_entry_data = Self::extract_evm_log(payload_v1)?;
            parse_evm_proof(proof_kind, chain_kind, log_entry_data)
        }
    }
```

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L163-174)
```rust
    fn request_matches_finality(request: &ForeignChainRpcRequest, finality: &MpcFinality) -> bool {
        match (request, finality) {
            (
                ForeignChainRpcRequest::Ethereum(args) | ForeignChainRpcRequest::Abstract(args),
                MpcFinality::Evm(finality),
            ) => args.finality == *finality,
            (ForeignChainRpcRequest::Starknet(args), MpcFinality::Starknet(finality)) => {
                args.finality == *finality
            }
            _ => false,
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L673-696)
```rust
    pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
        require!(
            args.storage_deposit_actions.len() <= 3,
            BridgeError::InvalidStorageAccountsLen.as_ref()
        );
        let mut main_promise = self.verify_proof(args.chain_kind, args.prover_args);

        let mut attached_deposit = env::attached_deposit();

        for action in &args.storage_deposit_actions {
            main_promise =
                main_promise.and(Self::check_or_pay_ft_storage(action, &mut attached_deposit));
        }

        main_promise.then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(attached_deposit)
                .with_static_gas(FIN_TRANSFER_CALLBACK_GAS)
                .fin_transfer_callback(
                    &args.storage_deposit_actions,
                    env::predecessor_account_id(),
                ),
        )
    }
```

**File:** near/omni-prover/mpc-omni-prover/src/tests.rs (L321-328)
```rust
#[test]
fn test_request_matches_finality_starknet_mismatch() {
    let request = ForeignChainRpcRequest::Starknet(test_starknet_request());
    assert!(!MpcOmniProver::request_matches_finality(
        &request,
        &MpcFinality::Starknet(StarknetFinality::AcceptedOnL2)
    ));
}
```
