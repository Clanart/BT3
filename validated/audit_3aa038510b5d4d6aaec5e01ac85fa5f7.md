### Title
Fee Recipient Cannot Claim Fee When Not a Trusted Relayer — (File: near/omni-bridge/src/lib.rs)

### Summary

The `claim_fee` function in the NEAR bridge is gated by `#[trusted_relayer]`, restricting callers to active trusted relayers. However, the `fee_recipient` embedded in the verified proof can be any account. When the `fee_recipient` is not a trusted relayer, the `#[trusted_relayer]` guard blocks the call before the inner `fee_recipient == *predecessor_account_id` check is ever reached. No trusted relayer can substitute for the fee_recipient either, because the callback enforces `fee_recipient == *predecessor_account_id`. The result is that the fee portion of the transfer is permanently locked in the bridge with no recovery path for ordinary users.

### Finding Description

`claim_fee` carries two independent access-control gates:

**Gate 1 — role check** (macro-level, evaluated first): [1](#0-0) 

The `#[trusted_relayer]` attribute (configured with bypass roles `Role::DAO` and `Role::UnrestrictedRelayer`) rejects any caller that is not an active trusted relayer before the function body executes.

**Gate 2 — identity check** (inside the callback): [2](#0-1) 

This requires the caller to be exactly the `fee_recipient` extracted from the proof.

Together, the two gates demand that the `fee_recipient` **is** the caller **and** is a trusted relayer. If the `fee_recipient` is any account that is not a trusted relayer, Gate 1 blocks every possible caller:

- The `fee_recipient` themselves cannot call `claim_fee` (not a trusted relayer).
- Any trusted relayer who tries to call `claim_fee` will pass Gate 1 but fail Gate 2 (they are not the `fee_recipient`).

The `fee_recipient` is sourced from the `FinTransferMessage` proof: [3](#0-2) 

On the EVM side, `feeRecipient` is a caller-supplied field in the `TransferMessagePayload` that is signed by the MPC and emitted in the `FinTransfer` event: [4](#0-3) [5](#0-4) 

The `fee_recipient` is set at `sign_transfer` time. Test code confirms that the **sender** (a non-relayer) can call `sign_transfer` and supply any `fee_recipient`: [6](#0-5) 

If the sender sets themselves as `fee_recipient`, or if a relayer resigns via `resign_trusted_relayer` after signing but before claiming, the fee is irrecoverably locked. The transfer message is never removed from `pending_transfers`: [7](#0-6) 

### Impact Explanation

The fee portion of every affected transfer is permanently frozen inside the NEAR bridge contract. The `pending_transfers` entry is never removed, so both the fee tokens and the storage slot are irrecoverably locked. No user-accessible function can rescue them; only a DAO-privileged `transfer_token_as_dao` call could intervene, requiring trusted-operator action. This matches the **Critical** impact class: *permanent freezing / irrecoverable lock of user or protocol funds in bridge flows*.

### Likelihood Explanation

Two realistic, unprivileged-user-reachable paths trigger this:

1. **Sender as fee_recipient**: A user initiating a transfer calls `sign_transfer` and sets themselves as `fee_recipient` (e.g., to reclaim the fee or to bridge without a relayer). They are not a trusted relayer, so `claim_fee` is permanently blocked.
2. **Relayer resignation with pending claims**: A relayer signs a transfer (setting themselves as `fee_recipient`), the transfer is finalized on the destination chain, and the relayer then calls `resign_trusted_relayer` before claiming the fee. After resignation they are no longer trusted and `claim_fee` is permanently blocked for that fee.

Both paths require no privileged access, no key compromise, and no colluding parties.

### Recommendation

Remove the `#[trusted_relayer]` guard from `claim_fee`. The `fee_recipient == *predecessor_account_id` check inside `claim_fee_callback` already ensures only the designated recipient can claim. The trusted-relayer gate is redundant for legitimate relayers and harmful for any non-relayer fee_recipient:

```rust
// Before
#[payable]
#[trusted_relayer]
#[pause(except(roles(Role::DAO)))]
pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {

// After
#[payable]
#[pause(except(roles(Role::DAO)))]
pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
```

### Proof of Concept

1. Alice initiates a NEAR→EVM transfer with `fee = 1_000_000` tokens. Tokens are locked in the bridge.
2. Alice calls `sign_transfer` with `fee_recipient = alice.near` (herself, a non-relayer).
3. The MPC signs the payload; the EVM `finTransfer` is executed; the `FinTransfer` event is emitted with `feeRecipient = "alice.near"`.
4. Alice calls `claim_fee` on NEAR with the proof of the `FinTransfer` event.
5. The `#[trusted_relayer]` macro fires first: Alice is not a trusted relayer → **transaction reverts**.
6. No trusted relayer can substitute: any trusted relayer calling `claim_fee` passes Gate 1 but hits `require!(fee_recipient == *predecessor_account_id)` → **reverts**.
7. The `pending_transfers` entry for Alice's transfer is never removed. The 1,000,000 fee tokens are permanently locked in the bridge with no user-accessible recovery path. [8](#0-7) [9](#0-8)

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

**File:** near/omni-bridge/src/lib.rs (L1075-1086)
```rust
        let Ok(ProverResult::FinTransfer(fin_transfer)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };

        let fee_recipient = fin_transfer.fee_recipient.unwrap_or_else(|| {
            env::panic_str(BridgeError::FeeRecipientNotSetOrEmpty.to_string().as_str());
        });

        require!(
            fee_recipient == *predecessor_account_id,
            BridgeError::OnlyFeeRecipientCanClaim.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1094-1094)
```rust
        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
```

**File:** evm/src/omni-bridge/contracts/BridgeTypes.sol (L5-14)
```text
    struct TransferMessagePayload {
        uint64 destinationNonce;
        uint8 originChain;
        uint64 originNonce;
        address tokenAddress;
        uint128 amount;
        address recipient;
        string feeRecipient;
        bytes message;
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L359-366)
```text
        emit BridgeTypes.FinTransfer(
            payload.originChain,
            payload.originNonce,
            payload.tokenAddress,
            payload.amount,
            payload.recipient,
            payload.feeRecipient
        );
```

**File:** near/omni-tests/src/init_transfer.rs (L140-160)
```rust
        // Transfer is signed by the relayer or the sender
        let signer = if is_relayer_sign {
            &env.relayer_account
        } else {
            &env.sender_account
        };

        signer
            .call(env.locker_contract.id(), "sign_transfer")
            .args_json(json!({
                "transfer_id": TransferId {
                    origin_chain: ChainKind::Near,
                    origin_nonce: transfer_message.origin_nonce,
                },
                "fee_recipient": env.relayer_account.id(),
                "fee": &Some(signing_fee.clone()),
            }))
            .max_gas()
            .transact()
            .await?
            .into_result()?;
```
