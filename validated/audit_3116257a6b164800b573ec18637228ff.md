### Title
Insufficient Amount Validation in `init_transfer` Allows Permanent Fund Lock via Decimal Normalization Rounding to Zero - (File: `near/omni-bridge/src/lib.rs`)

### Summary

The NEAR bridge's `init_transfer` function validates only that `fee < amount`, but does not validate that the post-fee amount survives decimal normalization to the destination chain. When `sign_transfer` is later called, `normalize_amount(amount_without_fee, decimals)` can round down to zero for tokens with large decimal differences between NEAR and the destination chain, causing `sign_transfer` to always revert with `InvalidAmountToTransfer`. Because the user's tokens are already locked in the bridge at `init_transfer` time and no cancel/refund path exists, the funds are permanently frozen.

### Finding Description

In `near/omni-bridge/src/lib.rs`, the `init_transfer` function (called from `ft_on_transfer`) stores the transfer and locks the user's tokens. Its only amount validation is:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [1](#0-0) 

This check passes as long as `fee < amount`, even if `amount - fee` is a dust value. The tokens are then locked in the bridge contract.

Later, when a relayer calls `sign_transfer`, the contract computes the destination-chain amount by normalizing the fee-subtracted amount through `normalize_amount`, which converts from NEAR's token decimals to the destination chain's decimals (e.g., dividing by `10^(near_decimals - dest_decimals)`). It then enforces:

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
``` [2](#0-1) 

If `amount - fee` is small relative to the decimal scaling factor, `normalize_amount` returns 0 and `sign_transfer` always panics. The transfer remains in `pending_transfers` indefinitely with no cancel or refund mechanism, permanently locking the user's tokens.

The `token_decimals` map stores per-token `Decimals` metadata used by `normalize_amount`: [3](#0-2) 

### Impact Explanation

**Critical — Permanent freezing of user funds.** A user who initiates a transfer with a small amount (relative to the decimal gap between NEAR and the destination chain) will have their tokens permanently locked in the bridge. The `sign_transfer` call will always revert, and there is no cancel or emergency-withdrawal path visible in the contract. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

### Likelihood Explanation

**Medium.** This is reachable by any unprivileged bridge user calling `ft_transfer_call` with a small amount. Tokens with large decimal differences between NEAR (e.g., 24 decimals) and a destination chain (e.g., USDC with 6 decimals) create an 18-order-of-magnitude scaling factor. Any transfer where `amount - fee < 10^18` (in NEAR token units) will normalize to 0. A user unfamiliar with the decimal mechanics can easily trigger this accidentally, and a malicious actor can trigger it deliberately to grief themselves or others (e.g., by front-running a storage deposit to force a specific transfer into this state).

### Recommendation

Add a pre-flight normalization check inside `init_transfer` (or `init_transfer_internal`) before storing the transfer and locking tokens. Specifically, after computing `transfer_message`, verify:

```rust
let decimals = self.token_decimals.get(&token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);
let normalized = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

This mirrors the fix recommended in the external report: validate the post-calculation value at the point of entry, not only at the point of use.

### Proof of Concept

1. A token `foo.near` has 24 decimals on NEAR and is mapped to a 6-decimal ERC-20 on Ethereum. The decimal normalization factor is `10^18`.
2. Alice calls `ft_transfer_call` on `foo.near` with `amount = 500_000_000_000_000_000` (5×10^17, i.e., 0.5 in 18-decimal terms), `fee = 0`, recipient = `eth:0xAlice`.
3. `init_transfer` checks `0 < 500_000_000_000_000_000` — passes. Transfer is stored; Alice's tokens are locked.
4. Relayer calls `sign_transfer`. `amount_without_fee() = 500_000_000_000_000_000`. `normalize_amount(500_000_000_000_000_000, decimals)` = `500_000_000_000_000_000 / 10^18 = 0`.
5. `require!(0 > 0, ...)` panics with `InvalidAmountToTransfer`.
6. No cancel function exists. Alice's tokens are permanently locked in the bridge. [4](#0-3) [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L228-228)
```rust
    pub token_decimals: LookupMap<OmniAddress, Decimals>,
```

**File:** near/omni-bridge/src/lib.rs (L447-485)
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
```

**File:** near/omni-bridge/src/lib.rs (L523-557)
```rust
    fn init_transfer(
        &mut self,
        sender_id: AccountId,
        signer_id: AccountId,
        token_id: AccountId,
        amount: U128,
        init_transfer_msg: InitTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );

        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```
