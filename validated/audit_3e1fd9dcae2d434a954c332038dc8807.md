### Title
`finish_withdraw_v2` Bypasses Pause Check, Enabling Irrecoverable Token Lock During Bridge Pause — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

The NEAR `omni-bridge` contract exposes `finish_withdraw_v2`, an alternative init-transfer path callable by deployed omni-token contracts, that lacks any pause guard. When the bridge is paused, this function still executes: it burns the user's tokens (via the calling token contract), increments the origin nonce, creates a pending `TransferMessage`, and emits an `InitTransferEvent`. Because `sign_transfer` is paused, the resulting pending transfer can never be signed or completed, permanently locking the user's funds.

---

### Finding Description

The bridge enforces a pause mechanism via `near-plugins`' `Pausable` trait. Every public entry point that initiates or finalizes a transfer is decorated with `#[pause(except(roles(Role::DAO)))]`:

- `ft_on_transfer` (handles `InitTransfer`, `FastFinTransfer`, `UtxoFinTransfer`) — paused [1](#0-0) 
- `sign_transfer` — paused [2](#0-1) 
- `fin_transfer` — paused [3](#0-2) 
- `claim_fee` — paused [4](#0-3) 
- `deploy_token` — paused [5](#0-4) 

However, `finish_withdraw_v2` carries **no pause decorator**:

```rust
pub fn finish_withdraw_v2(
    &mut self,
    #[serializer(borsh)] sender_id: &AccountId,
    #[serializer(borsh)] amount: u128,
    #[serializer(borsh)] recipient: String,
) {
    let token_id = env::predecessor_account_id();
    require!(self.is_deployed_token(&token_id),);
    ...
    env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
}
``` [6](#0-5) 

This function is the v2 withdrawal callback invoked by deployed omni-token contracts when a user burns tokens to bridge them to Ethereum. It:
1. Increments `current_origin_nonce` (state mutation)
2. Allocates a `destination_nonce` for `ChainKind::Eth`
3. Inserts a `TransferMessage` into `pending_transfers`
4. Emits `InitTransferEvent` (picked up by relayers)

The predecessor guard (`require!(self.is_deployed_token(&token_id))`) only restricts callers to legitimate deployed token contracts — it does not check the bridge's pause state. [7](#0-6) 

The `ALL_PAUSED` constant and the `Pausable` trait are correctly applied to every other user-facing path, making this omission an inconsistency directly analogous to the Blend flash-loan bypass: an alternative execution path achieves the same restricted operation (initiating a cross-chain transfer) without the status check.

---

### Impact Explanation

When the bridge is paused (e.g., due to a security incident):

1. A user calls the omni-token contract's withdraw/burn function.
2. The token contract burns the user's tokens — **irreversible on NEAR**.
3. The token contract calls `finish_withdraw_v2` on the bridge — **succeeds despite pause**.
4. A pending `TransferMessage` is created and `InitTransferEvent` is emitted.
5. Relayers observe the event but cannot call `sign_transfer` (paused). [2](#0-1) 
6. There is no `cancel_transfer` or refund path callable while paused.

The user's tokens are burned and the pending transfer is unresolvable for the duration of the pause. If the bridge is deprecated or permanently shut down after a critical incident, these funds are irrecoverably lost. Even in a recovery scenario, the accumulated pending transfers created during the pause complicate safe resumption.

This matches the **Critical** allowed impact: *Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.*

---

### Likelihood Explanation

Moderate. Any bridge pause event (security incident, emergency upgrade, MPC key rotation) creates the window. Users unaware of the pause state — or users whose wallets/dApps do not surface the bridge's pause status — will attempt withdrawals through the omni-token contract. The token contract burns their tokens before the bridge can reject the operation, because the bridge never rejects it. This is a realistic, user-triggered scenario during every pause event.

---

### Recommendation

Add the same pause guard used on all other init-transfer paths:

```rust
#[pause(except(roles(Role::DAO)))]
pub fn finish_withdraw_v2(
    &mut self,
    #[serializer(borsh)] sender_id: &AccountId,
    #[serializer(borsh)] amount: u128,
    #[serializer(borsh)] recipient: String,
) { ... }
```

Alternatively, the omni-token contract's withdraw function should query the bridge's pause state before burning tokens, so the burn is atomic with the bridge's ability to accept the transfer.

---

### Proof of Concept

1. Admin pauses the bridge (e.g., via `pa_pause_feature` or equivalent).
2. User calls the omni-token contract's `withdraw(amount, eth_recipient)` function.
3. Omni-token contract burns `amount` tokens from the user — irreversible.
4. Omni-token contract calls `finish_withdraw_v2(&bridge, sender_id, amount, eth_recipient)`.
5. `finish_withdraw_v2` executes without reverting: nonce incremented, `TransferMessage` inserted, `InitTransferEvent` emitted.
6. Relayer observes the event and attempts `sign_transfer` — reverts with `Pausable: paused`.
7. User's tokens are gone; the pending transfer sits unresolvable in `pending_transfers`.
8. If the bridge is never unpaused, the user permanently loses their funds with no recourse.

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

**File:** near/omni-bridge/src/lib.rs (L672-673)
```rust
    #[pause(except(roles(Role::DAO)))]
    pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
```

**File:** near/omni-bridge/src/lib.rs (L1056-1057)
```rust
    #[pause(except(roles(Role::DAO)))]
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
```

**File:** near/omni-bridge/src/lib.rs (L1137-1138)
```rust
    #[pause(except(roles(Role::DAO)))]
    pub fn deploy_token(&mut self, #[serializer(borsh)] args: DeployTokenArgs) -> Promise {
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
