### Title
Overly Strict Access Control on `claim_fee` Permanently Locks Relayer Fees When Fee Recipient Loses Trusted-Relayer Status — (`near/omni-bridge/src/lib.rs`)

---

### Summary

The `claim_fee` function in the NEAR omni-bridge is the only mechanism to distribute fees embedded in pending transfer messages. It is guarded by two compounding access-control restrictions: the `#[trusted_relayer]` attribute (caller must be a currently-registered trusted relayer) and an inline `require!` inside `claim_fee_callback` that enforces `fee_recipient == predecessor_account_id` (caller must be the exact fee recipient recorded in the proof). If the fee recipient loses their trusted-relayer status for any reason — stake expiry, voluntary unstake, or slashing — after finalizing a transfer on the destination chain but before calling `claim_fee`, the fees in the corresponding pending transfer message are permanently locked with no permissionless recovery path.

---

### Finding Description

`claim_fee` is the sole entry point for distributing fees that were committed into a pending transfer message at `sign_transfer` time:

```rust
#[payable]
#[trusted_relayer]                          // ← gate 1: caller must be a trusted relayer RIGHT NOW
#[pause(except(roles(Role::DAO)))]
pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
    self.verify_proof(args.chain_kind, args.prover_args).then(
        Self::ext(env::current_account_id())
            .with_attached_deposit(env::attached_deposit())
            .with_static_gas(CLAIM_FEE_CALLBACK_GAS)
            .claim_fee_callback(&env::predecessor_account_id()),
    )
}
``` [1](#0-0) 

Inside the callback, a second hard gate is enforced:

```rust
require!(
    fee_recipient == *predecessor_account_id,
    BridgeError::OnlyFeeRecipientCanClaim.as_ref()   // ← gate 2: only the exact fee recipient
);
``` [2](#0-1) 

Only after both gates pass does the callback call `remove_transfer_message` (removing the entry from `pending_transfers`) and then `send_fee_internal` (which calls `unlock_tokens_if_needed` and transfers/mints the fee): [3](#0-2) 

`send_fee_internal` decrements `locked_tokens` and mints or transfers the fee to the recipient: [4](#0-3) 

**Attack / failure scenario (no privileged collusion required):**

1. Relayer calls `sign_transfer`, recording themselves as `fee_recipient` in the transfer message.
2. Relayer finalizes the transfer on the destination chain (EVM/Solana/StarkNet).
3. Before the relayer calls `claim_fee`, their staking bond expires or is slashed — they are removed from the trusted-relayer set.
4. The relayer now fails gate 1 (`#[trusted_relayer]`). No other account can pass gate 2 (`fee_recipient == predecessor_account_id`).
5. The transfer message remains in `pending_transfers` indefinitely; the fee amount stays locked in the bridge with no permissionless way to release it.

There is no alternative function that allows anyone else (e.g., the DAO or any third party) to trigger fee distribution for a specific pending transfer. The only escape hatch is `transfer_token_as_dao`, which requires DAO governance action and is not a permissionless recovery path: [5](#0-4) 

---

### Impact Explanation

For **native (non-deployed) tokens**: the fee amount is part of `locked_tokens`. Because `unlock_tokens_if_needed` inside `send_fee_internal` is never called, the bridge permanently over-reports its locked balance, and the fee amount is irrecoverably frozen in the contract.

For **bridge-deployed tokens**: the fee is never minted, so the relayer's earned reward is permanently unclaimable.

In both cases the pending transfer message occupies on-chain storage forever, and the fee — which is protocol/relayer value committed by the user at transfer initiation — is permanently frozen. This matches the allowed impact: **"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."**

---

### Likelihood Explanation

The window between a relayer finalizing a transfer on the destination chain and calling `claim_fee` on NEAR is non-trivial (cross-chain latency, proof generation time). Relayer stake expiry or slashing during this window is a realistic operational event, not a theoretical one. No privileged collusion, key compromise, or chain-level attack is required — normal protocol lifecycle events (stake expiry, voluntary exit) are sufficient to trigger the lock.

---

### Recommendation

Remove the `#[trusted_relayer]` restriction from `claim_fee` and remove the `fee_recipient == predecessor_account_id` inline check. Because the fee recipient address is fixed inside the proof (signed by MPC), the fee will always be delivered to the correct address regardless of who triggers the call. Any account should be able to submit a valid proof and trigger fee distribution, exactly as the Spearbit recommendation for the analogous `collectFees` fix: *"anyone should be able to invoke the function and trigger collected fees delivery at any time."*

---

### Proof of Concept

1. Relayer `alice.near` calls `sign_transfer` for transfer `T`, recording `fee_recipient = alice.near`.
2. Alice finalizes transfer `T` on Ethereum.
3. Alice's staking bond expires; she is removed from the trusted-relayer registry.
4. Alice calls `claim_fee` with a valid proof of finalization → transaction panics at `#[trusted_relayer]` gate.
5. No other account can call `claim_fee` for transfer `T` because gate 2 (`fee_recipient == predecessor_account_id`) would reject them.
6. Transfer `T` remains in `pending_transfers` forever; the fee is permanently locked.

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

**File:** near/omni-bridge/src/lib.rs (L1083-1086)
```rust
        require!(
            fee_recipient == *predecessor_account_id,
            BridgeError::OnlyFeeRecipientCanClaim.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1094-1133)
```rust
        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);

        if let Some(origin_transfer_id) = transfer_message.origin_transfer_id.clone() {
            let mut fast_transfer = FastTransfer::from_transfer(
                transfer_message.clone(),
                self.get_token_id(&transfer_message.token),
            );
            fast_transfer.transfer_id = origin_transfer_id;

            if let Some(fast_transfer_status) = self.get_fast_transfer_status(&fast_transfer.id()) {
                // For fast transfers we need to wait for finalization of the first leg (Origin chain -> Near) before allowing fee claim.
                // This confirms that fast transfer was executed with correct parameters.
                // Othewise malicious relayer can create a fast transfer with arbitrary high fee and claim it here.
                if fast_transfer_status.finalised {
                    self.remove_fast_transfer(&fast_transfer.id());
                } else {
                    env::panic_str(BridgeError::FastTransferNotFinalised.to_string().as_str());
                }
            }
        }

        let token = self.get_token_id(&transfer_message.token);
        let token_address = self
            .get_token_address(transfer_message.get_destination_chain(), token.clone())
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });

        let denormalized_amount = Self::denormalize_amount(
            fin_transfer.amount.0,
            self.token_decimals
                .get(&token_address)
                .near_expect(BridgeError::TokenDecimalsNotFound),
        );
        // Fee includes both the user-specified fee and any dust lost during decimal
        // normalization (see `normalize_amount`). Since `denormalize(normalize(x)) <= x`
        // due to floor division, the difference naturally captures the normalization remainder.
        let fee = transfer_message.amount.0 - denormalized_amount;

        self.send_fee_internal(&transfer_message, fee_recipient, fee)
```

**File:** near/omni-bridge/src/lib.rs (L1511-1529)
```rust
    #[access_control_any(roles(Role::DAO))]
    pub fn transfer_token_as_dao(
        &mut self,
        token: AccountId,
        amount: U128,
        recipient: AccountId,
        msg: Option<String>,
    ) -> Promise {
        if let Some(msg) = msg {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_CALL_GAS)
                .ft_transfer_call(recipient, amount, None, msg)
        } else {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
        }
```

**File:** near/omni-bridge/src/lib.rs (L2684-2698)
```rust
        self.unlock_tokens_if_needed(transfer_message.get_destination_chain(), &token, token_fee);

        if token_fee > 0 {
            if self.is_deployed_token(&token) {
                ext_token::ext(token)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient, U128(token_fee), None)
                    .into()
            } else {
                ext_token::ext(token)
                    .with_static_gas(FT_TRANSFER_GAS)
                    .with_attached_deposit(ONE_YOCTO)
                    .ft_transfer(fee_recipient, U128(token_fee), None)
                    .into()
            }
```
