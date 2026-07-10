### Title
`normalize_amount` Floor-Division-to-Zero Permanently Locks User Funds in NEAR Bridge - (`File: near/omni-bridge/src/lib.rs`)

### Summary
`normalize_amount` uses integer floor division to scale a token amount from its origin-chain decimal precision to the bridge's internal precision. When a user initiates a NEAR→other-chain transfer with an amount smaller than the decimal scaling factor (`10^diff_decimals`), `normalize_amount` silently returns `0`. The tokens are already locked in the bridge at this point; the subsequent `sign_transfer` call then permanently fails with `InvalidAmountToTransfer`, and no cancellation path exists to recover the locked funds.

### Finding Description

`normalize_amount` is defined as:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

For any token where `origin_decimals > decimals` (e.g., a 24-decimal NEAR token bridged to an 18-decimal EVM representation, giving `diff_decimals = 6`), any user-supplied `amount < 10^6` (i.e., `< 1_000_000`) will produce `normalize_amount(...) = 0` due to Rust's integer floor division.

The transfer lifecycle on NEAR is:

**Step 1 – Tokens locked, no normalization check:**
`ft_on_transfer` → `init_transfer` stores the raw `amount` in `pending_transfers` and locks the tokens in the contract. No check is performed to verify that `normalize_amount(amount - fee) > 0`. [2](#0-1) 

**Step 2 – `sign_transfer` fails, tokens already locked:**
When the relayer later calls `sign_transfer`, it computes:

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [3](#0-2) 

If `amount - fee < 10^diff_decimals`, `normalize_amount` returns `0`, the `require!` panics, and `sign_transfer` is permanently unexecutable for this transfer.

**No recovery path:** A search of the contract reveals no `cancel_transfer`, `refund_transfer`, or equivalent function that would allow the user or any party to unlock the trapped tokens from `pending_transfers`. The only exit path for a NEAR→other-chain transfer is through `sign_transfer`, which is now permanently blocked. [4](#0-3) 

### Impact Explanation

Any user who sends a token amount below the decimal normalization threshold (e.g., `< 1_000_000` raw units for a 6-decimal-difference token) will have their tokens permanently locked in the NEAR bridge contract with no recovery mechanism. This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

This is reachable by any unprivileged bridge user via a standard `ft_transfer_call`. The condition is triggered whenever `amount - fee < 10^(origin_decimals - decimals)`. For tokens with large decimal differences (e.g., 24 vs 18 = factor of 1,000,000), any transfer of fewer than 1,000,000 raw units (which may be a very small but non-zero human-readable amount) triggers the bug. A user may do this accidentally (small test transfer, dust amount) or be induced to do so by a griefing attacker who knows the token's decimal configuration.

### Recommendation

Add a pre-flight check in `init_transfer` (before locking tokens) that verifies `normalize_amount(amount - fee, decimals) > 0`. This mirrors the fix pattern from the referenced SpeedJumpIrm report: detect the rounding-to-zero condition at the entry point, before any irreversible state change (token lock) occurs.

```rust
let normalized = Self::normalize_amount(
    amount.0.checked_sub(init_transfer_msg.fee.0)
        .near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

This ensures the transfer is rejected at `ft_on_transfer` time (causing `ft_transfer_call` to refund the tokens to the sender) rather than after the tokens are already locked.

### Proof of Concept

1. A token is registered with `origin_decimals = 24`, `decimals = 18` → `diff_decimals = 6`, scaling factor = `1_000_000`.
2. User calls `ft_transfer_call` with `amount = 500_000` (raw units), `fee = 0`.
3. `ft_on_transfer` → `init_transfer` succeeds: `500_000` tokens are locked in `pending_transfers`.
4. Relayer calls `sign_transfer`:
   - `amount_without_fee()` = `500_000`
   - `normalize_amount(500_000, {origin_decimals:24, decimals:18})` = `500_000 / 1_000_000` = **0** (floor division)
   - `require!(0 > 0, ...)` → **panics with `InvalidAmountToTransfer`**
5. `sign_transfer` can never succeed for this transfer ID.
6. No `cancel_transfer` function exists → `500_000` tokens are permanently locked. [1](#0-0) [3](#0-2)

### Citations

**File:** near/omni-bridge/src/lib.rs (L220-243)
```rust
pub struct Contract {
    pub factories: LookupMap<ChainKind, OmniAddress>,
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
    pub finalised_transfers: LookupSet<TransferId>,
    pub finalised_utxo_transfers: LookupSet<UnifiedTransferId>,
    pub fast_transfers: LookupMap<FastTransferId, FastTransferStatusStorage>,
    pub token_id_to_address: LookupMap<(ChainKind, AccountId), OmniAddress>,
    pub token_address_to_id: LookupMap<OmniAddress, AccountId>,
    pub token_decimals: LookupMap<OmniAddress, Decimals>,
    pub deployed_tokens: LookupSet<AccountId>,
    pub deployed_tokens_v2: LookupMap<AccountId, ChainKind>,
    pub token_deployer_accounts: LookupMap<ChainKind, AccountId>,
    pub mpc_signer: AccountId,
    pub current_origin_nonce: Nonce,
    // We maintain a separate nonce for each chain to optimize the storage usage on Solana by reducing the gaps.
    pub destination_nonces: LookupMap<ChainKind, Nonce>,
    pub accounts_balances: LookupMap<AccountId, StorageBalance>,
    pub wnear_account_id: AccountId,
    pub provers: UnorderedMap<ChainKind, AccountId>,
    pub init_transfer_promises: LookupMap<AccountId, CryptoHash>,
    pub utxo_chain_connectors: HashMap<ChainKind, UTXOChainConfig>,
    pub migrated_tokens: LookupMap<AccountId, AccountId>,
    pub locked_tokens: LookupMap<(ChainKind, AccountId), u128>,
}
```

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

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
