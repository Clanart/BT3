### Title
Inconsistent Pause Mechanism Allows Withdrawal Initiation During Emergency Pause - (File: near/omni-bridge/src/lib.rs)

### Summary
The `finish_withdraw_v2` function in `near/omni-bridge/src/lib.rs` lacks a pause check, while the primary transfer initiation path (`ft_on_transfer`) is properly gated by the `#[pause]` macro. This inconsistency allows any user to initiate a cross-chain withdrawal via a deployed token contract even when the bridge is paused, causing their tokens to be burned without any ability to complete the transfer, permanently locking user funds.

### Finding Description
The NEAR bridge contract uses the `near-plugins` `#[pause]` macro to gate critical operations. The `ft_on_transfer` function — the entry point for `InitTransfer`, `FastFinTransfer`, and `UtxoFinTransfer` — is decorated with `#[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]`, blocking unprivileged users from initiating transfers when the bridge is paused. [1](#0-0) 

However, `finish_withdraw_v2` at line 1315 carries no pause annotation whatsoever: [2](#0-1) 

This function is the callback entry point for deployed token contracts (e.g., omni-token) when a user calls `withdraw`. The access control is only that the predecessor must be a deployed token: [3](#0-2) 

The withdrawal flow is:
1. User calls `withdraw` on a deployed token contract
2. Token contract burns the user's tokens (irreversible)
3. Token contract calls `finish_withdraw_v2` on the bridge — **this succeeds even when paused**
4. `finish_withdraw_v2` creates a pending `TransferMessage` and emits `InitTransferEvent`
5. The subsequent `sign_transfer` call (which IS paused) cannot proceed [4](#0-3) 

The `sign_transfer` function is paused, so the pending transfer can never be finalized. There is no cancel or refund mechanism for pending transfers in the contract.

This is the direct analog to the reported vulnerability: `ft_on_transfer` (the standard outbound path) is paused, but `finish_withdraw_v2` (the deployed-token outbound path) is not, creating an inconsistency in the pause surface.

### Impact Explanation
When the bridge is paused:
- Users can still call `withdraw` on deployed token contracts
- Their tokens are burned by the token contract (irreversible)
- `finish_withdraw_v2` creates a pending transfer record (succeeds, no pause check)
- `sign_transfer` is paused — the transfer cannot be completed
- User tokens are permanently burned with no cross-chain settlement and no refund path

If the bridge remains paused for an extended period (e.g., during a security incident), user funds are irrecoverably locked. This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation
Medium. Any user holding tokens in a deployed bridge token contract can trigger this by calling `withdraw` while the bridge is paused. Users are not required to check the bridge's pause state before withdrawing — the token contract itself does not check the bridge pause state. Emergency pauses are precisely the scenario where users may rush to withdraw, making this timing realistic.

### Recommendation
Add a pause check to `finish_withdraw_v2`:

```rust
pub fn finish_withdraw_v2(
    &mut self,
    #[serializer(borsh)] sender_id: &AccountId,
    #[serializer(borsh)] amount: u128,
    #[serializer(borsh)] recipient: String,
) {
    self.assert_not_paused(); // Add this
    let token_id = env::predecessor_account_id();
    require!(self.is_deployed_token(&token_id));
    ...
}
```

Alternatively, the deployed token contract's `withdraw` function should query the bridge's pause state before burning tokens, so the burn is atomic with the ability to complete the transfer.

### Proof of Concept
1. Admin pauses the bridge (e.g., via `PauseManager` role)
2. User holds 1000 tokens in a deployed bridge token contract
3. User calls `withdraw(1000, "0xRecipient")` on the token contract
4. Token contract burns 1000 tokens from user's balance (irreversible)
5. Token contract calls `finish_withdraw_v2` on the bridge — **succeeds** (no pause check)
6. Bridge stores a pending `TransferMessage` and emits `InitTransferEvent`
7. User calls `sign_transfer` to request MPC signature — **reverts** (`Pausable: paused`)
8. User's 1000 tokens are burned; the pending transfer cannot be signed or cancelled
9. Funds are locked until the bridge is unpaused; if never unpaused, funds are permanently lost [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L252-253)
```rust
    #[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
```

**File:** near/omni-bridge/src/lib.rs (L446-447)
```rust
    #[pause(except(roles(Role::DAO)))]
    pub fn sign_transfer(
```

**File:** near/omni-bridge/src/lib.rs (L1314-1354)
```rust
    #[allow(clippy::needless_pass_by_value)]
    pub fn finish_withdraw_v2(
        &mut self,
        #[serializer(borsh)] sender_id: &AccountId,
        #[serializer(borsh)] amount: u128,
        #[serializer(borsh)] recipient: String,
    ) {
        let token_id = env::predecessor_account_id();
        require!(self.is_deployed_token(&token_id),);

        self.current_origin_nonce += 1;
        let destination_nonce = self.get_next_destination_nonce(ChainKind::Eth);

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount: U128(amount),
            recipient: OmniAddress::Eth(
                H160::from_str(&recipient).near_expect(BridgeError::InvalidRecipientAddress),
            ),
            fee: Fee {
                fee: U128(0),
                native_fee: U128(0),
            },
            sender: OmniAddress::Near(sender_id.clone()),
            msg: String::new(),
            destination_nonce,
            origin_transfer_id: None,
        };

        let required_storage_balance =
            self.add_transfer_message(transfer_message.clone(), sender_id.clone());

        self.update_storage_balance(
            env::current_account_id(),
            required_storage_balance,
            NearToken::from_yoctonear(0),
        );

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
    }
```
