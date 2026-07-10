### Title
`claim_fee_callback` enforces caller-must-equal-`fee_recipient`, permanently locking bridge fees when `fee_recipient` is a NEAR contract unable to invoke `claim_fee` — (`near/omni-bridge/src/lib.rs`)

---

### Summary

The `claim_fee_callback` function in the NEAR bridge contract enforces that only the exact `fee_recipient` account (as embedded in the cross-chain proof) may call `claim_fee`. If the `fee_recipient` is a NEAR smart contract that does not implement a method to call `claim_fee`, the accumulated bridge fees are permanently unclaimable with no admin recovery path.

---

### Finding Description

In `near/omni-bridge/src/lib.rs`, the public entry point `claim_fee` passes `env::predecessor_account_id()` into its callback: [1](#0-0) 

Inside `claim_fee_callback`, the `fee_recipient` is extracted from the proof (`FinTransferMessage`) and compared against the caller: [2](#0-1) 

The `fee_recipient` field is a NEAR `AccountId` (`Option<AccountId>`) embedded in the `FinTransferMessage` proof: [3](#0-2) 

This value originates from the `FinalizeTransferPayload` submitted on the destination chain (e.g., Solana), where it is a free-form `Option<String>` set by whoever submits the finalization transaction: [4](#0-3) 

The `fee_recipient` string is serialized into the Wormhole message and later verified on NEAR as part of the proof. There is no on-chain constraint preventing the `fee_recipient` from being set to a NEAR smart contract address. If that contract does not implement a method to call `claim_fee` on the bridge, the fees stored in the bridge are permanently locked. There is no admin override, no alternative claim path, and no timeout-based recovery in the contract.

Additionally, `claim_fee` carries a `#[trusted_relayer]` guard: [5](#0-4) 

This means the `fee_recipient` must also be a trusted relayer to call `claim_fee`. If the `fee_recipient` is a smart contract that is a trusted relayer but lacks the ability to call `claim_fee`, the fees are doubly unclaimable: the contract cannot satisfy the `predecessor_account_id == fee_recipient` check because it cannot initiate the call.

---

### Impact Explanation

**Critical/High — Permanent freezing of bridge fees.**

Bridge fees accumulate in the NEAR bridge contract's token balances (via `send_fee_internal`): [6](#0-5) 

If the `fee_recipient` is a NEAR smart contract that cannot call `claim_fee`, those token balances are irrecoverably locked. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Moderate.** The `fee_recipient` is set by whoever submits the finalization transaction on the destination chain (Solana, EVM, StarkNet). Professional relayer operations commonly use smart contract wallets, multisigs, or DAO treasuries as their fee collection addresses. Setting `fee_recipient` to such a contract address is a natural operational choice. If that contract does not implement a `claim_fee` call to the NEAR bridge, the fees are permanently stuck. No special attacker capability is required — this is triggered by ordinary relayer configuration.

---

### Recommendation

Remove the `fee_recipient == predecessor_account_id` restriction and instead send the fee directly to the `fee_recipient` address without requiring the recipient to initiate the claim. The proof already cryptographically binds the `fee_recipient` to the finalized transfer, so there is no need to require the recipient to be the caller. Alternatively, allow any caller to trigger the fee claim on behalf of the `fee_recipient` when the `fee_recipient` is a smart contract (detectable via `env::is_account_id_valid` or a code-hash check).

---

### Proof of Concept

1. A relayer finalizes a transfer on Solana, setting `fee_recipient = Some("multisig.relayer.near")` in `FinalizeTransferPayload`. `multisig.relayer.near` is a NEAR multisig contract that is a trusted relayer but has no method to call `claim_fee` on the bridge.
2. The Wormhole message is posted; the proof is later submitted to NEAR via `claim_fee`.
3. `claim_fee_callback` decodes the proof, extracts `fee_recipient = "multisig.relayer.near"`.
4. The check `require!(fee_recipient == *predecessor_account_id, ...)` requires the caller to be `multisig.relayer.near`.
5. `multisig.relayer.near` has no method to call `claim_fee`; no EOA can satisfy the check.
6. The fees for this transfer are permanently locked in the bridge contract with no recovery path. [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1054-1064)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args).then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(env::attached_deposit())
                .with_static_gas(CLAIM_FEE_CALLBACK_GAS)
                .claim_fee_callback(&env::predecessor_account_id()),
        )
    }
```

**File:** near/omni-bridge/src/lib.rs (L1079-1086)
```rust
        let fee_recipient = fin_transfer.fee_recipient.unwrap_or_else(|| {
            env::panic_str(BridgeError::FeeRecipientNotSetOrEmpty.to_string().as_str());
        });

        require!(
            fee_recipient == *predecessor_account_id,
            BridgeError::OnlyFeeRecipientCanClaim.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1133-1134)
```rust
        self.send_fee_internal(&transfer_message, fee_recipient, fee)
    }
```

**File:** near/omni-types/src/prover_result.rs (L22-27)
```rust
pub struct FinTransferMessage {
    pub transfer_id: TransferId,
    pub fee_recipient: Option<AccountId>,
    pub amount: U128,
    pub emitter_address: OmniAddress,
}
```

**File:** solana/programs/bridge_token_factory/src/state/message/finalize_transfer.rs (L11-16)
```rust
pub struct FinalizeTransferPayload {
    pub destination_nonce: u64,
    pub transfer_id: TransferId,
    pub amount: u128,
    pub fee_recipient: Option<String>,
}
```
