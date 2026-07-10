### Title
Unsafe Default Finality (`EvmFinality::Latest` / `StarknetFinality::AcceptedOnL2`) in `MpcOmniProver` Enables Unauthorized Token Minting via Source-Chain Reorganization — (File: `near/omni-prover/mpc-omni-prover/src/lib.rs`)

---

### Summary

`MpcOmniProver.init()` hardcodes `EvmFinality::Latest` for `ChainKind::Abs` (Abstract chain) and `StarknetFinality::AcceptedOnL2` for `ChainKind::Strk` (Starknet). These are the weakest available finality levels for their respective chains. Because `request_matches_finality` enforces an **exact equality** check, the prover will only accept proofs at these weak finality levels and will reject any proof at a stronger level. If the source chain reorganizes after the NEAR bridge has minted tokens, the minted tokens have no corresponding locked collateral on the source chain — an unauthorized mint of bridged assets.

---

### Finding Description

In `near/omni-prover/mpc-omni-prover/src/lib.rs`, the `init` function hardcodes two insecure finality defaults:

```rust
pub fn init(mpc_contract_id: AccountId) -> Self {
    let mut finalities = HashMap::new();
    finalities.insert(ChainKind::Abs, MpcFinality::Evm(EvmFinality::Latest));   // weakest EVM finality
    finalities.insert(
        ChainKind::Strk,
        MpcFinality::Starknet(StarknetFinality::AcceptedOnL2),                  // not yet proven on L1
    );
    ...
}
```

`EvmFinality::Latest` corresponds to the most recent block with zero confirmations — the direct analog of `minBlockHeight: 0` from the reference report. `StarknetFinality::AcceptedOnL2` means the transaction is accepted by the Starknet sequencer but has not yet been proven on Ethereum L1.

The `request_matches_finality` function enforces strict equality:

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

This means the bridge will **only** accept proofs with `EvmFinality::Latest` for Abstract chain and will **reject** proofs with `EvmFinality::Finalized` or `EvmFinality::Safe`. The bridge is locked into the weakest finality level.

The contrast with Ethereum is explicit in the test suite: `test_evm_request()` uses `EvmFinality::Finalized` for Ethereum, while `abs_testnet_evm_request()` uses `EvmFinality::Latest` for Abstract chain — confirming the protocol is aware of the distinction.

Additionally, `set_finality` is marked `#[private]`, meaning it can only be called by the contract itself (predecessor == current_account_id), not by an external admin. The insecure defaults are therefore effectively immutable without a full contract upgrade.

---

### Impact Explanation

Abstract chain is a ZKSync-based EVM L2. On ZKSync, `EvmFinality::Latest` means the transaction is in the latest L2 block that has not yet been committed to Ethereum L1. L2 sequencer reorganizations can remove such transactions. If a reorganization occurs after the NEAR bridge has minted tokens based on a proof from an unfinalized block:

- The minted NEAR tokens have no corresponding locked collateral on the source chain.
- The bridge's collateralization is broken.
- This constitutes **unauthorized minting of bridged assets** — a Critical impact per the allowed scope.

For Starknet, `StarknetFinality::AcceptedOnL2` means the transaction is accepted by the Starknet sequencer but not yet proven on Ethereum L1. Starknet sequencer reorganizations, while less frequent, are possible and would produce the same outcome.

---

### Likelihood Explanation

Abstract chain (ZKSync-based L2) has a centralized sequencer. L2 sequencer reorganizations are a known operational risk for ZKSync-based chains — they can occur without a 51% attack, simply as a result of sequencer restarts, bugs, or deliberate reordering. A user who initiates a transfer and immediately submits the proof at `EvmFinality::Latest` is exposed to this risk on every transfer. The bridge's nonce tracking prevents replay of the same proof, but does not prevent the scenario where the source-chain transaction is reorganized away after NEAR tokens have already been minted.

---

### Recommendation

1. Change the default finality for `ChainKind::Abs` from `EvmFinality::Latest` to `EvmFinality::Finalized` to ensure transactions are finalized on Ethereum L1 before being accepted by the bridge.
2. Change the default finality for `ChainKind::Strk` from `StarknetFinality::AcceptedOnL2` to `StarknetFinality::AcceptedOnL1` to ensure transactions are proven on Ethereum L1 before being accepted.
3. Remove the `#[private]` restriction from `set_finality` (or add a DAO/admin-gated wrapper) so that finality settings can be updated without a full contract upgrade.
4. Analogous to the reference report's fix: document per-chain safe finality defaults and warn or refuse to operate with weaker-than-safe settings.

---

### Proof of Concept

1. User initiates a transfer on Abstract chain; the transaction lands in the latest L2 block (block N, not yet committed to L1).
2. The MPC network queries the Abstract chain RPC at `EvmFinality::Latest`, confirms the transaction exists, and signs the `ForeignTxSignPayload`.
3. User calls `MpcOmniProver::verify_proof` with the signed payload.
4. `request_matches_finality` passes because the proof's finality field is `EvmFinality::Latest`, exactly matching the configured value.
5. The MPC contract confirms the payload hash; `verify_callback` parses the EVM log and returns `ProverResult::InitTransfer`.
6. The NEAR bridge mints tokens to the recipient and records the nonce as finalized.
7. The Abstract chain sequencer reorganizes; block N is removed and the deposit transaction no longer exists on the source chain.
8. The user holds NEAR-minted tokens with no corresponding locked collateral on Abstract chain — the bridge supply is unbacked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** near/omni-prover/mpc-omni-prover/src/tests.rs (L87-101)
```rust
fn test_evm_request() -> EvmRpcRequest {
    EvmRpcRequest {
        tx_id: EvmTxId([0xab; 32]),
        extractors: vec![EvmExtractor::Log { log_index: 0 }],
        finality: EvmFinality::Finalized,
    }
}

fn abs_testnet_evm_request() -> EvmRpcRequest {
    EvmRpcRequest {
        tx_id: abs_testnet_tx_id(),
        extractors: vec![EvmExtractor::Log { log_index: 3 }],
        finality: EvmFinality::Latest,
    }
}
```
