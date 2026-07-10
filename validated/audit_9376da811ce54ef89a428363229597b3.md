### Title
User Tokens Permanently Locked When Transfer Amount Normalizes to Zero in `sign_transfer` — (File: near/omni-bridge/src/lib.rs)

---

### Summary

When a user initiates a bridge transfer with an amount smaller than the decimal precision gap between the origin and destination chains, `sign_transfer` panics with `InvalidAmountToTransfer` **after** the tokens have already been locked in `init_transfer`. Because no cancel or refund path exists for this state, the user's tokens are permanently irrecoverable.

---

### Finding Description

**Step 1 — User initiates transfer with a sub-threshold amount.**

`init_transfer` (called via `ft_transfer_call`) accepts any amount satisfying `fee.fee < amount`. There is no minimum-amount guard relative to the decimal precision gap. [1](#0-0) 

Tokens are immediately locked (or burned for deployed tokens) and a `TransferMessage` is stored in contract state: [2](#0-1) 

**Step 2 — `sign_transfer` normalizes the amount to zero and panics.**

`sign_transfer` calls `normalize_amount`, which performs floor division by `10^(origin_decimals − decimals)`: [3](#0-2) 

For a token registered with `origin_decimals = 24` and `decimals = 9` (NEAR → Solana), the divisor is `10^15`. Any user amount below `10^15` base units normalizes to `0`. The subsequent guard then panics: [4](#0-3) 

The panic aborts the `sign_transfer` call entirely. The MPC signer is never invoked, so `sign_transfer_callback` is never reached.

**Step 3 — No recovery path exists.**

`sign_transfer_callback` removes the `TransferMessage` only when the fee is zero **and** MPC signing succeeds: [5](#0-4) 

Since the callback is never reached, the `TransferMessage` remains in storage permanently. The locked tokens can never be unlocked through normal protocol flows:

- `update_transfer_fee` only allows **increasing** the fee, which makes `amount_without_fee` smaller — worsening the situation. [6](#0-5) 

- `claim_fee` requires a proof from the destination chain of a completed `FinTransfer` event — impossible since no transfer was ever signed. [7](#0-6) 

- There is no public `cancel_transfer` function in the contract.

The `set_locked_tokens` DAO function can adjust the accounting balance, but for **deployed (bridged) tokens** the tokens were already burned — they are gone regardless of any accounting correction. [8](#0-7) 

---

### Impact Explanation

**Critical — Permanent, irrecoverable lock of user funds in the bridge vault flow.**

Any user who sends a transfer amount below `10^(origin_decimals − decimals)` base units will have their tokens permanently locked in the bridge contract (native tokens) or permanently burned (deployed/bridged tokens). The `TransferMessage` occupies storage indefinitely with no protocol-level mechanism to reclaim the underlying assets.

Concrete example: a token registered with `origin_decimals = 24`, `decimals = 9` (NEAR → Solana). Any transfer amount below `10^15` yocto-units (= 0.001 NEAR-equivalent) triggers the lock. For tokens with `origin_decimals = 24`, `decimals = 6` (NEAR → a 6-decimal EVM token), the threshold rises to `10^18` base units.

---

### Likelihood Explanation

**Medium.** The condition is reachable by any unprivileged user without special knowledge:

1. A user testing the bridge with a small "dust" amount.
2. A user bridging a high-precision NEAR token to a low-precision destination (e.g., Solana SPL tokens with 6–9 decimals), where the threshold is non-trivial.
3. A user making a rounding mistake in the amount.

No privileged role, leaked key, or external dependency compromise is required. The only prerequisite is that the token is registered with `origin_decimals > decimals`, which is the normal configuration for NEAR-native tokens bridging to EVM/Solana.

---

### Recommendation

Add a minimum-amount check in `init_transfer` (or `ft_on_transfer`) that validates the amount will survive normalization before locking tokens:

```rust
let token_address = self.get_token_address(destination_chain, token_id.clone())
    .near_expect(BridgeError::FailedToGetTokenAddress);
let decimals = self.token_decimals.get(&token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);
let normalized = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

This mirrors the existing guard in `sign_transfer` but places it **before** tokens are locked, so the `ft_transfer_call` can return the tokens to the sender rather than locking them.

---

### Proof of Concept

1. Register a token with `origin_decimals = 24`, `decimals = 9` (NEAR → Solana).
2. User calls `ft_transfer_call` with `amount = 500_000_000_000_000` (< `10^15`), `fee = 0`, valid Solana recipient.
3. `init_transfer` passes the `fee < amount` check, increments `current_origin_nonce`, stores the `TransferMessage`, and calls `lock_tokens_if_needed` — tokens are now locked.
4. Relayer calls `sign_transfer` for the resulting `TransferId`.
5. `normalize_amount(500_000_000_000_000, Decimals{decimals:9, origin_decimals:24})` = `500_000_000_000_000 / 10^15` = `0`.
6. `require!(0 > 0, ...)` panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
7. `sign_transfer_callback` is never invoked; `TransferMessage` remains; locked tokens are permanently frozen.
8. All subsequent calls to `sign_transfer` for the same `TransferId` repeat step 6 and panic identically. [9](#0-8) [3](#0-2) [10](#0-9)

### Citations

**File:** near/omni-bridge/src/lib.rs (L399-402)
```rust
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
                );
```

**File:** near/omni-bridge/src/lib.rs (L475-485)
```rust
        let amount_to_transfer = Self::normalize_amount(
            transfer_message
                .amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );

        require!(
            amount_to_transfer > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L554-557)
```rust
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L655-658)
```rust
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }
```

**File:** near/omni-bridge/src/lib.rs (L1054-1063)
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

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L38-44)
```rust
    #[access_control_any(roles(Role::DAO, Role::TokenLockController))]
    pub fn set_locked_tokens(&mut self, args: Vec<SetLockedTokenArgs>) {
        for arg in args {
            self.locked_tokens
                .insert(&(arg.chain_kind, arg.token_id), &arg.amount.0);
        }
    }
```
