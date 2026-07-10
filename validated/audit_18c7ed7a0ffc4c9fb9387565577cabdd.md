### Title
Unsafe Default Finality `StarknetFinality::AcceptedOnL2` in `MpcOmniProver::init` Enables Pre-Proof Transfer Finalization - (File: near/omni-prover/mpc-omni-prover/src/lib.rs)

### Summary

`MpcOmniProver` is initialized with `StarknetFinality::AcceptedOnL2` as the hardcoded default finality for Starknet (`ChainKind::Strk`). `AcceptedOnL2` is the weakest Starknet finality level — it means the transaction has only been accepted by the Starknet sequencer and has **not** been proven and settled on Ethereum L1. The prover enforces this finality as a strict equality check, meaning it will only accept proofs at this unsafe level. A Starknet → NEAR bridge transfer can be finalized on NEAR based on a Starknet transaction that is not cryptographically final, creating a double-spend window if the L2 state is reverted.

### Finding Description

In `MpcOmniProver::init`, the Starknet finality is hardcoded to `AcceptedOnL2`:

```rust
pub fn init(mpc_contract_id: AccountId) -> Self {
    let mut finalities = HashMap::new();
    finalities.insert(ChainKind::Abs, MpcFinality::Evm(EvmFinality::Latest));
    finalities.insert(
        ChainKind::Strk,
        MpcFinality::Starknet(StarknetFinality::AcceptedOnL2),
    );
    ...
}
``` [1](#0-0) 

Starknet has two finality levels:
- `AcceptedOnL2`: Transaction accepted by the Starknet sequencer. **Not cryptographically final.** Can be reverted before an L1 proof is submitted.
- `AcceptedOnL1`: Transaction proven and settled on Ethereum L1. Cryptographically final.

The `verify_proof` function enforces a strict equality check between the submitted proof's finality and the configured finality:

```rust
require!(
    Self::request_matches_finality(&payload_v1.request, finality),
    ProverError::FinalityMismatch.as_ref()
);
``` [2](#0-1) 

The `request_matches_finality` function performs exact equality:

```rust
(ForeignChainRpcRequest::Starknet(args), MpcFinality::Starknet(finality)) => {
    args.finality == *finality
}
``` [3](#0-2) 

This means the prover **only** accepts proofs with `AcceptedOnL2` finality — it rejects `AcceptedOnL1`. The own test suite confirms this: `test_request_matches_finality_starknet_mismatch` asserts that `AcceptedOnL2` does NOT match `AcceptedOnL1`, and the `starknet_sepolia_request()` test fixture uses `AcceptedOnL2` as the expected finality for the deployed prover. [4](#0-3) [5](#0-4) 

The `set_finality` function is marked `#[private]`, meaning it can only be called by the contract itself (admin-controlled cross-contract call). The unsafe default is therefore the operative value unless explicitly overridden post-deployment. [6](#0-5) 

### Impact Explanation

When a Starknet → NEAR transfer is processed through `MpcOmniProver`:

1. User calls `init_transfer` on Starknet — tokens are locked or burned.
2. The Starknet transaction reaches `AcceptedOnL2` state (sequencer-accepted, not L1-proven).
3. A relayer submits the MPC proof to NEAR's `fin_transfer`, which calls `MpcOmniProver::verify_proof`.
4. The proof passes the finality check (matches `AcceptedOnL2`).
5. The NEAR bridge's `fin_transfer_callback` mints or releases tokens to the recipient on NEAR. [7](#0-6) 

6. If the Starknet L2 state is subsequently reverted (before the L1 proof is submitted), the original tokens are returned to the attacker on Starknet.
7. The attacker now holds tokens on both NEAR and Starknet — an unauthorized mint / double-spend of bridged assets.

The NEAR bridge marks the transfer nonce as finalized and cannot undo the mint/release. The impact is **direct unauthorized mint or release of bridged assets** across chains.

### Likelihood Explanation

Starknet's sequencer is currently centralized (operated by StarkWare). An L2 reorg requires sequencer action, which is not a routine event. However:

- The security model of a cross-chain bridge must not rely on sequencer honesty. `AcceptedOnL2` is explicitly a pre-proof, non-final state by Starknet's own specification.
- Bugs in the sequencer or forced reorgs are possible without requiring deliberate malice.
- The correct finality for bridge security is `AcceptedOnL1`, which is cryptographically proven on Ethereum L1 and cannot be reverted.
- The existing test `test_request_matches_finality_starknet_mismatch` demonstrates the codebase authors are aware of the distinction between `AcceptedOnL2` and `AcceptedOnL1`, yet the default remains the weaker level.

Likelihood is **medium**: requires sequencer-level action, but the protocol design unnecessarily accepts pre-proof state for irreversible cross-chain asset release.

### Recommendation

Change the default Starknet finality in `MpcOmniProver::init` from `AcceptedOnL2` to `AcceptedOnL1`:

```rust
finalities.insert(
    ChainKind::Strk,
    MpcFinality::Starknet(StarknetFinality::AcceptedOnL1), // was AcceptedOnL2
);
``` [8](#0-7) 

`AcceptedOnL1` guarantees the Starknet transaction has been proven and settled on Ethereum L1, making it cryptographically irreversible before the NEAR bridge finalizes the transfer.

### Proof of Concept

1. Deploy `MpcOmniProver` using the default `init` (Starknet finality = `AcceptedOnL2`).
2. Attacker calls `init_transfer` on the Starknet bridge contract — tokens are burned/locked on Starknet.
3. The Starknet transaction reaches `AcceptedOnL2` state.
4. Relayer (or attacker acting as relayer) submits the MPC-signed proof to NEAR's `fin_transfer` with `proof_kind = InitTransfer` and `StarknetFinality::AcceptedOnL2` in the payload.
5. `MpcOmniProver::verify_proof` passes the finality check; MPC network signs off; `verify_callback` returns a valid `ProverResult::InitTransfer`.
6. NEAR bridge's `fin_transfer_callback` mints bridged tokens to the attacker's NEAR address.
7. Starknet sequencer reverts the L2 transaction (before L1 proof submission) — attacker's tokens are returned on Starknet.
8. Attacker holds tokens on both NEAR (minted) and Starknet (returned) — net gain of one full token amount.

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

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L73-76)
```rust
    #[private]
    pub fn set_finality(&mut self, chain_kind: ChainKind, finality: MpcFinality) {
        self.finalities.insert(chain_kind, finality);
    }
```

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L95-98)
```rust
        require!(
            Self::request_matches_finality(&payload_v1.request, finality),
            ProverError::FinalityMismatch.as_ref()
        );
```

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L169-171)
```rust
            (ForeignChainRpcRequest::Starknet(args), MpcFinality::Starknet(finality)) => {
                args.finality == *finality
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

**File:** near/omni-prover/mpc-omni-prover/src/tests.rs (L472-480)
```rust
fn starknet_sepolia_request() -> StarknetRpcRequest {
    StarknetRpcRequest {
        tx_id: StarknetTxId(hex_to_starknet_felt(
            "0x0592d937f74565b8c42c5603083e5536fdcd8e585b5fef5cd5c2c04b65cd80e5",
        )),
        finality: StarknetFinality::AcceptedOnL2,
        extractors: vec![StarknetExtractor::Log { log_index: 3 }],
    }
}
```

**File:** near/omni-bridge/src/lib.rs (L700-745)
```rust
    pub fn fin_transfer_callback(
        &mut self,
        #[serializer(borsh)] storage_deposit_actions: &Vec<StorageDepositAction>,
        #[serializer(borsh)] predecessor_account_id: AccountId,
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
```
