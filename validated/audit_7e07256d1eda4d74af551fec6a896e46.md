### Title
Decimal Normalization Floor Division Permanently Locks User Funds When `amount_without_fee < 10^(origin_decimals - decimals)` - (File: `near/omni-bridge/src/lib.rs`)

### Summary

`sign_transfer` applies `normalize_amount` (floor division) to `amount_without_fee` before signing. When the user's net transfer amount is smaller than the decimal-scaling divisor, the result is 0 and `sign_transfer` always panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`. Because `init_transfer` does not pre-validate the normalized amount, the user's tokens are already locked in the bridge with no recovery path.

### Finding Description

`normalize_amount` performs integer floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

`sign_transfer` calls this on `amount_without_fee()` and then hard-reverts if the result is 0:

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(amount_to_transfer > 0, BridgeError::InvalidAmountToTransfer.as_ref());
``` [2](#0-1) 

`init_transfer`, however, only validates `fee < amount` — it never checks whether the normalized net amount would be positive:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [3](#0-2) 

The user's tokens are transferred into the bridge contract via `ft_transfer_call` → `ft_on_transfer` → `init_transfer` and the `TransferMessage` is stored in `pending_transfers` — all in a committed transaction. [4](#0-3) 

There is no `cancel_transfer` or refund path in the contract. The only progression path is `sign_transfer`, which will always revert for this transfer. The tokens are permanently locked. [5](#0-4) 

### Impact Explanation

**Permanent freezing of user funds.** Once a user initiates a transfer whose `amount_without_fee` is below the decimal-scaling threshold, their tokens are irrecoverably locked in the NEAR bridge contract. `sign_transfer` will always revert, no signed payload is ever produced, and no destination-chain release can occur. There is no cancel or refund function.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

### Likelihood Explanation

The condition is triggered whenever:

- A token has high `origin_decimals` on NEAR (e.g., 24 for wNEAR-style tokens) and the destination chain representation uses fewer decimals (e.g., 9 for Solana, 6 for USDC-style EVM tokens).
- The user's `amount_without_fee` is less than `10^(origin_decimals − decimals)`.

Concrete example — NEAR token (24 decimals) → Solana (9 decimals):
- `diff_decimals = 15`, divisor = `10^15`
- Any transfer where `amount_without_fee < 1_000_000_000_000_000` normalizes to 0.
- A user transferring 999,999,999,999,999 raw units (a valid, non-zero amount that passes the `fee < amount` check) will have their tokens permanently locked.

This is reachable by any unprivileged bridge user via the public `ft_transfer_call` → `ft_on_transfer` entry point. [4](#0-3) 

### Recommendation

Add a normalized-amount check inside `init_transfer` (or `init_transfer_internal`) before committing the transfer message and locking tokens. Reject the transfer early if `normalize_amount(amount_without_fee, decimals) == 0`, so the user's tokens are returned via the `ft_transfer_call` refund mechanism (returning the full amount from `ft_on_transfer`).

```rust
// Inside init_transfer, after resolving token_address and decimals:
let normalized = Self::normalize_amount(
    amount.0.checked_sub(fee.fee.0).unwrap_or(0),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

### Proof of Concept

1. Register a NEAR-native token with `origin_decimals = 24`, `decimals = 9` (Solana destination).
2. Call `ft_transfer_call` with `amount = 500_000_000_000_000` (< `10^15`) and `fee = 0`.
3. `init_transfer` passes: `fee (0) < amount (5×10^14)` ✓. Transfer message stored, tokens locked.
4. Relayer calls `sign_transfer` for this transfer.
5. `normalize_amount(500_000_000_000_000, {origin: 24, decimals: 9})` = `500_000_000_000_000 / 10^15` = **0**.
6. `require!(0 > 0, ...)` → **panics** with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
7. No MPC signing request is ever issued. Transfer message remains in `pending_transfers`. User's `500_000_000_000_000` raw units are permanently locked with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** near/omni-bridge/src/lib.rs (L252-283)
```rust
    #[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
        let token_id = env::predecessor_account_id();
        let parsed_msg: BridgeOnTransferMsg = serde_json::from_str(&msg)
            .or_else(|_| serde_json::from_str(&msg).map(BridgeOnTransferMsg::InitTransfer))
            .near_expect(BridgeError::ParseMsg);

        // We can't trust sender_id to pay for storage as it can be spoofed.
        let signer_id = env::signer_account_id();
        let promise_or_promise_index_or_value = match parsed_msg {
            BridgeOnTransferMsg::InitTransfer(init_transfer_msg) => {
                self.init_transfer(sender_id, signer_id, token_id, amount, init_transfer_msg)
            }
            BridgeOnTransferMsg::FastFinTransfer(fast_fin_transfer_msg) => {
                self.fast_fin_transfer(token_id, amount, signer_id, fast_fin_transfer_msg)
            }
            BridgeOnTransferMsg::UtxoFinTransfer(utxo_fin_transfer_msg) => self.utxo_fin_transfer(
                token_id,
                amount,
                &signer_id,
                &sender_id,
                utxo_fin_transfer_msg,
            ),
            BridgeOnTransferMsg::SwapMigratedToken => {
                self.swap_migrated_token(sender_id, token_id, amount)
                    .detach();
                PromiseOrPromiseIndexOrValue::Value(U128(0))
            }
        };

        promise_or_promise_index_or_value.as_return();
    }
```

**File:** near/omni-bridge/src/lib.rs (L447-521)
```rust
    pub fn sign_transfer(
        &mut self,
        transfer_id: TransferId,
        fee_recipient: Option<AccountId>,
        fee: &Option<Fee>,
    ) -> Promise {
        let transfer_message = self.get_transfer_message(transfer_id);

        if let Some(fee) = &fee {
            require!(
                &transfer_message.fee == fee,
                BridgeError::InvalidFee.as_ref()
            );
        }

        let token_address = self
            .get_token_address(
                transfer_message.get_destination_chain(),
                self.get_token_id(&transfer_message.token),
            )
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });

        let decimals = self
            .token_decimals
            .get(&token_address)
            .near_expect(BridgeError::TokenDecimalsNotFound);
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

        let message = DestinationChainMsg::from_json(&transfer_message.msg)
            .and_then(|s| s.destination_msg())
            .unwrap_or_default();

        let transfer_payload = TransferMessagePayload {
            prefix: PayloadType::TransferMessage,
            destination_nonce: transfer_message.destination_nonce,
            transfer_id,
            token_address,
            amount: U128(amount_to_transfer),
            recipient: transfer_message.recipient,
            fee_recipient,
            message,
        };

        let payload = near_sdk::env::keccak256_array(
            transfer_payload
                .encode_hashable()
                .near_expect(BridgeError::Borsh),
        );

        ext_signer::ext(self.mpc_signer.clone())
            .with_static_gas(MPC_SIGNING_GAS)
            .with_attached_deposit(env::attached_deposit())
            .sign(SignRequest {
                payload,
                path: SIGN_PATH.to_owned(),
                key_version: 0,
            })
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(SIGN_TRANSFER_CALLBACK_GAS)
                    .sign_transfer_callback(transfer_payload, &transfer_message.fee),
            )
    }
```

**File:** near/omni-bridge/src/lib.rs (L554-557)
```rust
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
