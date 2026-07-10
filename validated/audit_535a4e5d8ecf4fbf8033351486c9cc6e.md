### Title
Fee-on-Transfer Token Accounting Mismatch Breaks EVM Bridge Collateralization - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
`OmniBridge.initTransfer()` records the caller-supplied `amount` parameter in the emitted `InitTransfer` event without verifying the actual tokens received. For fee-on-transfer ERC20 tokens, the contract receives `amount - transfer_fee` but the event (and therefore the NEAR-side proof) records `amount`. The NEAR bridge then mints the full `amount` on NEAR, permanently undercollateralizing the EVM locker by the token's transfer fee on every such bridge transaction.

### Finding Description

In `OmniBridge.initTransfer()`, when the token is a standard (non-bridge, non-custom-minter) ERC20, the contract executes:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount
);
```

followed immediately by:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // ← caller-supplied, not actual-received
    fee,
    nativeFee,
    recipient,
    message
);
``` [1](#0-0) 

There is no balance-before / balance-after check. For a fee-on-transfer ERC20, `safeTransferFrom` deducts a transfer fee from the transferred amount, so the bridge contract actually receives `amount - token_transfer_fee`. The emitted event still carries the full `amount`.

On the NEAR side, `fin_transfer_callback` constructs the `TransferMessage` directly from the proof of the EVM event:

```rust
let transfer_message = TransferMessage {
    amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
    fee: Self::denormalize_fee(&init_transfer.fee, decimals),
    // ...
};
``` [2](#0-1) 

`init_transfer.amount` is taken verbatim from the proof (the EVM event value), so NEAR mints the full `amount` worth of tokens (split between recipient and relayer fee), while the EVM locker only holds `amount - token_transfer_fee`.

### Impact Explanation

Every `initTransfer` call with a fee-on-transfer ERC20 creates a collateral shortfall equal to the token's transfer fee. The shortfall accumulates across transactions. When users later bridge tokens back from NEAR to EVM, the EVM locker eventually cannot satisfy all redemptions — the last redeemers lose funds permanently. This directly breaks bridge collateralization and causes irrecoverable loss of user funds.

**Impact class**: High — Balance/fee/accounting corruption that breaks bridge collateralization.

### Likelihood Explanation

The EVM `initTransfer` function has no token whitelist; any ERC20 address is accepted. Fee-on-transfer tokens (e.g., tokens with reflection mechanics, deflationary tokens) are a well-established ERC20 variant. A user only needs to call `initTransfer` with such a token after it has been registered on the NEAR side via `log_metadata`. No privileged access is required.

### Recommendation

Record the actual received amount by measuring the balance before and after the transfer:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(actualReceived == amount, "Fee-on-transfer tokens not supported");
```

Alternatively, explicitly document and enforce that fee-on-transfer tokens are unsupported, and add an on-chain check or registry that rejects such tokens at registration time.

### Proof of Concept

1. Deploy a fee-on-transfer ERC20 token `FOT` that deducts 1% on every transfer.
2. Register `FOT` with the NEAR bridge via `log_metadata`.
3. Call `OmniBridge.initTransfer(FOT, 1000, 10, 0, "near:alice", "")`.
   - `safeTransferFrom` moves 1000 FOT from the caller; the bridge receives 990 (1% fee = 10 tokens).
   - The `InitTransfer` event records `amount = 1000`.
4. A NEAR relayer submits the proof to `fin_transfer`. NEAR mints 990 tokens to Alice and 10 tokens to the relayer (total: 1000 minted).
5. The EVM locker holds only 990 FOT but 1000 FOT-equivalent tokens exist on NEAR.
6. Repeat N times; the shortfall grows to `N × 10` FOT.
7. When NEAR holders bridge back, the EVM locker runs out of FOT before all NEAR tokens are redeemed. The last `N × 10` FOT worth of NEAR tokens are permanently unclaimable. [3](#0-2) [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-437)
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
    }
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
