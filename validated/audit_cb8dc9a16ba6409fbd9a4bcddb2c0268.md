### Title
Insufficient Source-Chain Finality in `MpcOmniProver` Enables Unauthorized Minting via Reorg — (`File: near/omni-prover/mpc-omni-prover/src/lib.rs`)

### Summary

`MpcOmniProver` is hardcoded at initialization to accept proofs at `EvmFinality::Latest` for `ChainKind::Abs` (Abstract chain) and `StarknetFinality::AcceptedOnL2` for `ChainKind::Strk`. Both finality levels are pre-L1-settlement and susceptible to sequencer-level reorgs. Because the NEAR bridge irreversibly records a transfer as finalized the moment a valid proof is accepted, a reorg on the source chain after proof submission creates unbacked bridged supply on the destination chain.

### Finding Description

In `MpcOmniProver::init()`, two chains are registered with weak finality levels:

```rust
finalities.insert(ChainKind::Abs, MpcFinality::Evm(EvmFinality::Latest));
finalities.insert(
    ChainKind::Strk,
    MpcFinality::Starknet(StarknetFinality::AcceptedOnL2),
);
``` [1](#0-0) 

`verify_proof()` enforces that the finality in the caller-supplied `sign_payload` matches the configured finality for the chain:

```rust
require!(
    Self::request_matches_finality(&payload_v1.request, finality),
    ProverError::FinalityMismatch.as_ref()
);
``` [2](#0-1) 

`request_matches_finality` performs an equality check — it accepts `Latest` for Abstract and `AcceptedOnL2` for Starknet, and rejects anything stronger: [3](#0-2) 

The test suite confirms this: `Finalized` and `Safe` are explicitly rejected for Abstract, and `AcceptedOnL1` is rejected for Starknet: [4](#0-3) 

Once the MPC prover returns a valid result, the NEAR bridge records the transfer as permanently finalized via `add_fin_transfer`, which panics on any duplicate — making the finalization irreversible:

```rust
require!(
    self.finalised_transfers.insert(transfer_id),
    BridgeError::TransferAlreadyFinalised.as_ref()
);
``` [5](#0-4) 

**`EvmFinality::Latest` (Abstract chain):** Abstract is a ZK rollup on Ethereum. The sequencer can reorg L2 blocks before the ZK proof is submitted and verified on Ethereum L1. A transaction at `Latest` finality has not been committed to L1 and can be removed by a sequencer reorg.

**`StarknetFinality::AcceptedOnL2` (Starknet):** `AcceptedOnL2` means the Starknet sequencer has accepted the transaction but it has not yet been proven and settled on Ethereum L1. The sequencer can reorg L2 blocks before L1 settlement. `AcceptedOnL1` is the safe finality level for Starknet.

By contrast, Ethereum (`ChainKind::Eth`) is correctly configured to require `EvmFinality::Finalized`: [6](#0-5) 

### Impact Explanation

If a source-chain reorg removes a user's `initTransfer` transaction after the bridge has already finalized the corresponding `fin_transfer` on NEAR (or another destination chain), the user retains their tokens on the source chain while also receiving bridged tokens on the destination chain. This creates unbacked bridged supply — tokens minted or released on the destination chain with no corresponding locked or burned collateral on the source chain. This is a direct unauthorized mint of bridged assets.

The `process_fin_transfer_to_near` path mints or unlocks tokens on NEAR: [7](#0-6) 

And `process_fin_transfer_to_other_chain` similarly releases tokens toward other destination chains: [8](#0-7) 

Both paths call `add_fin_transfer` first, making the finalization permanent before any token movement.

### Likelihood Explanation

The attack path is reachable by an unprivileged bridge user:

1. User calls `initTransfer` on Abstract chain or Starknet, locking/burning tokens.
2. The MPC network observes the transaction at `Latest` / `AcceptedOnL2` finality and signs the `ForeignTxSignPayload`.
3. User (or any relayer) submits the signed proof to the NEAR bridge via `fin_transfer` → `verify_proof` → `MpcOmniProver::verify_proof`.
4. NEAR bridge finalizes the transfer, minting/releasing tokens on the destination.
5. A sequencer reorg on Abstract or Starknet removes the original source transaction.
6. The user's tokens are restored on the source chain; the destination tokens remain.

For Abstract chain, sequencer reorgs are a known property of ZK rollups before L1 proof submission. For Starknet, the sequencer is centralized (StarkWare) and can reorg L2 state before L1 settlement. Neither scenario requires a 51% attack — only a sequencer-level reorg, which is an inherent property of these L2 architectures at the finality levels configured.

The window of vulnerability is the time between MPC signing (at `Latest`/`AcceptedOnL2`) and L1 finalization of the source transaction. This window can be minutes to hours depending on the chain.

### Recommendation

1. **Abstract chain:** Change the configured finality from `EvmFinality::Latest` to `EvmFinality::Finalized` (or at minimum `EvmFinality::Safe`). This requires waiting for the Abstract chain's L1 settlement, but eliminates the reorg window.

2. **Starknet:** Change the configured finality from `StarknetFinality::AcceptedOnL2` to `StarknetFinality::AcceptedOnL1`. This ensures the Starknet transaction is proven and settled on Ethereum L1 before the bridge accepts the proof.

3. If lower latency is required for Abstract/Starknet, implement a time-lock or challenge period on the destination side before releasing funds, analogous to optimistic bridge designs.

### Proof of Concept

```
// Attacker flow for Abstract chain:

// Step 1: Attacker calls initTransfer on Abstract chain (block N, Latest)
// Tokens are locked/burned on Abstract chain

// Step 2: MPC network observes tx at EvmFinality::Latest (block N)
// MPC signs ForeignTxSignPayload with finality=Latest

// Step 3: Attacker submits proof to NEAR bridge
// MpcOmniProver::verify_proof() accepts because:
//   finalities[ChainKind::Abs] == MpcFinality::Evm(EvmFinality::Latest)
//   payload.request.finality == EvmFinality::Latest  ✓ matches

// Step 4: NEAR bridge calls add_fin_transfer() → irreversible
// Tokens minted/released on destination chain

// Step 5: Abstract chain sequencer reorgs block N
// initTransfer transaction is removed
// Attacker's tokens are restored on Abstract chain

// Result: Attacker holds tokens on BOTH Abstract chain AND destination chain
// Unbacked supply created on destination
```

The hardcoded initialization is the root cause: [9](#0-8)

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

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L95-98)
```rust
        require!(
            Self::request_matches_finality(&payload_v1.request, finality),
            ProverError::FinalityMismatch.as_ref()
        );
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

**File:** near/omni-prover/mpc-omni-prover/src/tests.rs (L268-275)
```rust
#[test]
fn test_request_matches_finality_ethereum_match() {
    let request = ForeignChainRpcRequest::Ethereum(test_evm_request());
    assert!(MpcOmniProver::request_matches_finality(
        &request,
        &MpcFinality::Evm(EvmFinality::Finalized)
    ));
}
```

**File:** near/omni-prover/mpc-omni-prover/src/tests.rs (L299-328)
```rust
#[test]
fn test_request_matches_finality_abstract_mismatch() {
    let request = ForeignChainRpcRequest::Abstract(abs_testnet_evm_request());
    assert!(!MpcOmniProver::request_matches_finality(
        &request,
        &MpcFinality::Evm(EvmFinality::Finalized)
    ));
    assert!(!MpcOmniProver::request_matches_finality(
        &request,
        &MpcFinality::Evm(EvmFinality::Safe)
    ));
}

#[test]
fn test_request_matches_finality_starknet_match() {
    let request = ForeignChainRpcRequest::Starknet(test_starknet_request());
    assert!(MpcOmniProver::request_matches_finality(
        &request,
        &MpcFinality::Starknet(StarknetFinality::AcceptedOnL1)
    ));
}

#[test]
fn test_request_matches_finality_starknet_mismatch() {
    let request = ForeignChainRpcRequest::Starknet(test_starknet_request());
    assert!(!MpcOmniProver::request_matches_finality(
        &request,
        &MpcFinality::Starknet(StarknetFinality::AcceptedOnL2)
    ));
}
```

**File:** near/omni-bridge/src/lib.rs (L1867-1875)
```rust
    #[allow(clippy::too_many_lines, clippy::ptr_arg)]
    fn process_fin_transfer_to_near(
        &mut self,
        recipient: AccountId,
        predecessor_account_id: &AccountId,
        transfer_message: TransferMessage,
        storage_deposit_actions: &Vec<StorageDepositAction>,
    ) -> Promise {
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
```

**File:** near/omni-bridge/src/lib.rs (L1980-1985)
```rust
    fn process_fin_transfer_to_other_chain(
        &mut self,
        predecessor_account_id: AccountId,
        transfer_message: TransferMessage,
    ) {
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
```

**File:** near/omni-bridge/src/lib.rs (L2226-2234)
```rust
    fn add_fin_transfer(&mut self, transfer_id: &TransferId) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.finalised_transfers.insert(transfer_id),
            BridgeError::TransferAlreadyFinalised.as_ref()
        );
        env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
    }
```
