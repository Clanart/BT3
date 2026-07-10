### Title
No User-Callable Cancel or Timeout for Pending Transfers After Tokens Are Locked/Burned — (`near/omni-bridge/src/lib.rs`)

---

### Summary

When a user initiates a NEAR-origin transfer, their tokens are immediately and irrevocably locked or burned inside `init_transfer_internal`. The only way to advance the transfer to the destination chain is for a **trusted relayer** to call `sign_transfer`, which is gated by the `#[trusted_relayer]` macro. If the entire trusted relayer set becomes unavailable (all resign, are revoked by DAO, or are offline), there is no user-callable cancel function, no expiry mechanism, and no escape hatch. The user's funds are permanently frozen in the bridge contract with no path to recovery.

---

### Finding Description

The NEAR-to-EVM (and NEAR-to-Solana, NEAR-to-StarkNet) transfer flow proceeds as follows:

**Step 1 — User initiates transfer (unprivileged):**
`ft_on_transfer` → `init_transfer` → `init_transfer_internal`

Inside `init_transfer_internal`, tokens are immediately locked or burned before the transfer is stored:

```rust
// near/omni-bridge/src/lib.rs:1850-1857
if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
    self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
    self.lock_tokens_if_needed(
        transfer_message.get_destination_chain(),
        &token_id,
        transfer_message.amount.0,
    );
```

For deployed (bridged) tokens, `burn_tokens_if_needed` burns them immediately and irreversibly. For native tokens, they are locked in the contract. The transfer message is then stored in `pending_transfers`.

**Step 2 — Only a trusted relayer can advance the transfer:**

```rust
// near/omni-bridge/src/lib.rs:444-447
#[payable]
#[trusted_relayer]
#[pause(except(roles(Role::DAO)))]
pub fn sign_transfer(
```

The `#[trusted_relayer]` macro enforces that only accounts with active trusted relayer status (or `Role::DAO` / `Role::UnrestrictedRelayer`, both privileged roles) can call `sign_transfer`. A regular user cannot call it.

The trusted relayer macro is configured at the `impl` block level:

```rust
// near/omni-bridge/src/lib.rs:245-249
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedRelayer),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**Step 3 — No cancel or expiry exists:**

Searching the entire contract, there is no public function that allows a user to cancel a pending transfer and recover their locked/burned tokens. The `remove_transfer_message` function is internal and only called by privileged flows (`claim_fee_callback`, `sign_transfer_callback` when fee is zero, `submit_transfer_to_utxo_chain_connector`). There is no deadline or timeout field on `TransferMessage`.

The `pending_transfers` map stores transfers indefinitely:

```rust
// near/omni-bridge/src/lib.rs:222
pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
```

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

- For **deployed (bridged) tokens**: tokens are burned at `init_transfer_internal` time. If no trusted relayer ever calls `sign_transfer`, the tokens are permanently destroyed with no corresponding mint on the destination chain. The user suffers a total loss.
- For **native tokens**: tokens are locked in the bridge contract. If no trusted relayer calls `sign_transfer`, the tokens remain locked forever with no user-callable recovery path.

In both cases, the `pending_transfers` entry persists indefinitely, and the user has no on-chain mechanism to reclaim their assets.

---

### Likelihood Explanation

The trusted relayer set is a permissioned, staked set of accounts. Scenarios that cause all relayers to be unavailable include:

1. All active relayers call `resign_trusted_relayer` simultaneously (each relayer can resign unilaterally).
2. The DAO calls `reject_relayer_application` on all active relayers (DAO can revoke any relayer and claim their stake).
3. All relayer infrastructure goes offline (no malicious intent required — operational failure suffices).
4. A relayer selectively refuses to process a specific transfer (censorship of a targeted user).

In any of these scenarios, transfers that were already initiated (tokens already locked/burned) have no recovery path. The `#[pause]` mechanism can stop new transfers but cannot rescue already-pending ones.

---

### Recommendation

1. **Add a user-callable cancel function** that allows the original sender to cancel a pending transfer after a configurable timeout period, returning locked tokens or minting equivalent tokens back to the sender.
2. **Add a deadline field** to `TransferMessage` so that expired transfers can be identified and cancelled.
3. **Allow the transfer sender to call `sign_transfer` directly** (bypassing the trusted relayer restriction) after a timeout, so they can self-serve the MPC signature request.
4. Alternatively, allow any account (not just trusted relayers) to call `sign_transfer`, since the MPC signature itself is the security guarantee — the relayer restriction adds liveness risk without adding security.

---

### Proof of Concept

1. User calls `ft_transfer_call` on a NEAR token contract, routing to the bridge with an `InitTransfer` message targeting an EVM recipient.
2. `init_transfer_internal` burns (for deployed tokens) or locks (for native tokens) the user's tokens and stores the transfer in `pending_transfers`.
3. All trusted relayers resign by calling `resign_trusted_relayer`, or the DAO revokes them all via `reject_relayer_application`.
4. The user attempts to call `sign_transfer` — the `#[trusted_relayer]` guard rejects the call because the user is not a trusted relayer.
5. The user has no other on-chain function to call. The transfer sits in `pending_transfers` forever. For deployed tokens, the burned supply is gone. For native tokens, the locked balance is permanently inaccessible. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L222-222)
```rust
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
```

**File:** near/omni-bridge/src/lib.rs (L245-249)
```rust
#[trusted_relayer(
    bypass_roles(Role::DAO, Role::UnrestrictedRelayer),
    manager_roles(Role::DAO, Role::RelayerManager),
    config_roles(Role::DAO)
)]
```

**File:** near/omni-bridge/src/lib.rs (L444-447)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn sign_transfer(
```

**File:** near/omni-bridge/src/lib.rs (L1829-1865)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
    }
```

**File:** near/omni-bridge/src/lib.rs (L2194-2211)
```rust
    fn remove_transfer_message(&mut self, transfer_id: TransferId) -> TransferMessage {
        let storage_usage = env::storage_usage();
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(&transfer.owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(&transfer.owner, &storage);
        }

        transfer.message
    }
```
