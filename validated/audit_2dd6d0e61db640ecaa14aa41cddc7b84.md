Now I have enough context to analyze the vulnerability. Let me look at the exact `finTransfer` path to confirm the impact.

### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Causes Bridge Undercollateralization and Permanent Fund Lock - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary

`OmniBridge.initTransfer` records and emits the caller-supplied `amount` parameter rather than the actual tokens received after a Fee-on-Transfer (FoT) token's transfer fee is deducted. The NEAR bridge mints the full nominal `amount` to the destination recipient based on the emitted event, while the EVM vault only holds `amount × (1 − fee_rate)`. This permanently undercollateralizes the EVM bridge for that token, causing the last user(s) to bridge back to EVM to be unable to withdraw their funds.

### Finding Description

In `OmniBridge.initTransfer`, when the token is a plain ERC20 (not a bridge token and not a custom minter), the contract executes:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // caller-supplied nominal amount
);
``` [1](#0-0) 

Immediately after, the function emits the `InitTransfer` event using the same caller-supplied `amount`, not the actual balance delta received:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,          // nominal, not actual received
    fee,
    nativeFee,
    recipient,
    message
);
``` [2](#0-1) 

For a FoT token with a 1% transfer fee, a call with `amount = 1000` causes the bridge to receive only 990 tokens, but the event records 1000.

On the NEAR side, `fin_transfer_callback` decodes the proof of this event and constructs a `TransferMessage` using `init_transfer.amount` directly from the proof data (the nominal 1000):

```rust
let transfer_message = TransferMessage {
    ...
    amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
    ...
};
``` [3](#0-2) 

NEAR then mints or transfers 1000 tokens to the recipient. The EVM bridge is now undercollateralized by 10 tokens per transfer.

When users later bridge tokens back from NEAR to EVM, `finTransfer` attempts:

```solidity
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount   // full amount from signed payload
);
``` [4](#0-3) 

Because the vault holds less than the total outstanding NEAR-side supply, the final user(s) to redeem will encounter a revert due to insufficient balance, permanently locking their funds.

### Impact Explanation

The EVM bridge vault becomes undercollateralized for any FoT token bridged via `initTransfer`. Every `initTransfer` call with a FoT token widens the deficit. When users bridge back to EVM, `finTransfer` will succeed for early redeemers but revert for the last redeemer(s), permanently locking their funds in the NEAR bridge contract with no recovery path. This matches:

- **Critical**: Permanent freezing / irrecoverable lock of user funds in bridge flows.
- **High**: Balance/fee/accounting corruption that breaks bridge collateralization.

### Likelihood Explanation

The `initTransfer` function imposes no restriction on which ERC20 tokens can be bridged — any token not registered as a bridge token or custom minter goes through the vulnerable `safeTransferFrom` path. FoT tokens are a well-known token class (e.g., tokens with redistribution mechanics, deflationary tokens, tokens with protocol fees). Any user can trigger this by calling `initTransfer` with such a token. No privileged access is required.

### Recommendation

Measure the actual balance received by comparing the contract's balance before and after the `safeTransferFrom`, and use the delta as the canonical `amount` for event emission and downstream accounting:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
// Use actualReceived in place of amount for the event and extension call
```

Alternatively, document that FoT tokens are explicitly unsupported and add a registry/allowlist of permitted tokens so that FoT tokens cannot be submitted to `initTransfer`.

### Proof of Concept

1. A FoT token `FOT` with a 1% transfer fee is deployed on EVM. It is not registered as a bridge token or custom minter, so it takes the plain `safeTransferFrom` path.
2. Alice calls `initTransfer(FOT, 10_000, fee=0, nativeFee=0, recipient="alice.near", "")`.
3. The bridge receives `9_900 FOT` (1% fee deducted), but emits `InitTransfer(..., amount=10_000, ...)`.
4. A relayer submits proof of this event to the NEAR bridge via `fin_transfer`. `fin_transfer_callback` reads `amount = 10_000` from the proof and mints `10_000 FOT` (NEAR-side wrapped) to `alice.near`.
5. Alice now holds 10_000 NEAR-side tokens; the EVM vault holds only 9_900 EVM-side tokens. The bridge is undercollateralized by 100 tokens.
6. Alice bridges back 10_000 NEAR-side tokens to EVM. The NEAR bridge burns them and signs a `TransferMessage` for `amount = 10_000`.
7. The relayer calls `finTransfer` on EVM with `payload.amount = 10_000`. The vault only holds 9_900 tokens → `safeTransfer` reverts → Alice's funds are permanently locked. [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L351-354)
```text
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-436)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }

        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }

        initTransferExtension(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

        emit BridgeTypes.InitTransfer(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
```

**File:** near/omni-bridge/src/lib.rs (L700-746)
```rust
    pub fn fin_transfer_callback(
        &mut self,
        #[serializer(borsh)] storage_deposit_actions: &Vec<StorageDepositAction>,
        #[serializer(borsh)] predecessor_account_id: AccountId,
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
    }
```
