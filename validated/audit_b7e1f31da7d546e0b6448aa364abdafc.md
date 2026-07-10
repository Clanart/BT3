### Title
`finish_withdraw_v2` Bypasses Pause Mechanism, Allowing Transfer Initiation When Bridge Is Paused — (File: `near/omni-bridge/src/lib.rs`)

### Summary

The NEAR omni-bridge contract enforces a pause check on all user-facing transfer functions via the `#[pause]` macro, but `finish_withdraw_v2` — a legacy withdrawal entry point callable by any deployed token contract — has no pause guard. When the bridge is paused, users can still initiate NEAR→ETH transfers through this path, burning their tokens on the token-contract side while the resulting pending transfer cannot be finalized (because `sign_transfer` is paused). This locks user funds for the duration of the pause.

### Finding Description

Every user-facing function that initiates or finalizes a bridge transfer carries a `#[pause]` or `#[pause(except(roles(...)))]` attribute:

- `ft_on_transfer` — `#[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]`
- `sign_transfer` — `#[pause(except(roles(Role::DAO)))]`
- `fin_transfer` — `#[pause(except(roles(Role::DAO)))]`
- `claim_fee` — `#[pause(except(roles(Role::DAO)))]`
- `deploy_token` — `#[pause(except(roles(Role::DAO)))]`
- `bind_token` — `#[pause(except(roles(Role::DAO)))]`
- `log_metadata` — `#[pause(except(roles(Role::DAO)))]`

The sole exception is `finish_withdraw_v2`:

```rust
// near/omni-bridge/src/lib.rs  line 1315
#[allow(clippy::needless_pass_by_value)]
pub fn finish_withdraw_v2(
    &mut self,
    #[serializer(borsh)] sender_id: &AccountId,
    #[serializer(borsh)] amount: u128,
    #[serializer(borsh)] recipient: String,
) {
    let token_id = env::predecessor_account_id();
    require!(self.is_deployed_token(&token_id),);   // only access check

    self.current_origin_nonce += 1;
    let destination_nonce = self.get_next_destination_nonce(ChainKind::Eth);
    let transfer_message = TransferMessage { ... };
    ...
    env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
}
```

There is no `#[pause]` attribute and no manual pause check. Any deployed token contract can call this function at any time, regardless of the bridge's pause state. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

### Impact Explanation

When the bridge is paused (e.g., in response to a security incident):

1. A user calls `withdraw` on a deployed token contract (a legacy bridge token).
2. The token contract burns the user's tokens and cross-contract-calls `finish_withdraw_v2` on the bridge.
3. `finish_withdraw_v2` succeeds — it increments `current_origin_nonce`, increments `destination_nonces[Eth]`, stores a `TransferMessage` in `pending_transfers`, and emits an `InitTransferEvent`.
4. The user's tokens are now permanently burned on the NEAR side.
5. The relayer cannot call `sign_transfer` (it is paused for non-DAO callers), so the MPC signature is never produced and the transfer is never finalized on Ethereum.
6. The user's funds are irrecoverably locked for the duration of the pause. If the bridge is shut down permanently, the funds are lost.

This matches **Critical: Permanent freezing / irrecoverable lock of user funds in bridge flows**. [5](#0-4) [6](#0-5) 

### Likelihood Explanation

- The bridge pause mechanism is explicitly designed to halt all user-initiated transfers during emergencies.
- Any user holding a legacy deployed bridge token (e.g., tokens from the old Rainbow Bridge) can trigger this path by calling `withdraw` on that token contract.
- No special role or permission is required beyond holding such a token.
- The window of exposure is the entire duration of any pause event.

### Recommendation

Add the same pause guard used by all other user-facing transfer functions:

```rust
#[pause]                          // or #[pause(except(roles(Role::DAO)))]
pub fn finish_withdraw_v2(
    &mut self,
    #[serializer(borsh)] sender_id: &AccountId,
    #[serializer(borsh)] amount: u128,
    #[serializer(borsh)] recipient: String,
) {
    ...
}
```

Alternatively, add an explicit comment documenting why this function is intentionally exempt from the pause mechanism (if that is the design intent), analogous to the recommendation in the referenced external report.

### Proof of Concept

1. Admin calls `pa_pause_feature` (or equivalent) to pause the bridge.
2. User holds tokens in a deployed legacy bridge token contract (predecessor is in `deployed_tokens` or `deployed_tokens_v2`).
3. User calls `withdraw(amount, recipient_eth_address)` on the token contract.
4. Token contract burns `amount` tokens from the user and calls:
   ```
   omni_bridge.finish_withdraw_v2(sender_id=user, amount=X, recipient="0x...")
   ```
5. `finish_withdraw_v2` executes without reverting — no pause check exists.
6. `current_origin_nonce` is incremented, a `TransferMessage` is stored in `pending_transfers`, and `InitTransferEvent` is emitted.
7. User's tokens are burned. Relayer attempts `sign_transfer` but it reverts with `Paused` for non-DAO callers.
8. Funds are locked with no recourse until the bridge is unpaused. [7](#0-6) [8](#0-7)

### Citations

**File:** near/omni-bridge/src/lib.rs (L252-253)
```rust
    #[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
```

**File:** near/omni-bridge/src/lib.rs (L444-447)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn sign_transfer(
```

**File:** near/omni-bridge/src/lib.rs (L672-673)
```rust
    #[pause(except(roles(Role::DAO)))]
    pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
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

**File:** near/omni-bridge/src/lib.rs (L1356-1358)
```rust
    pub fn is_deployed_token(&self, token: &AccountId) -> bool {
        self.deployed_tokens.contains(token) || self.deployed_tokens_v2.contains_key(token)
    }
```
