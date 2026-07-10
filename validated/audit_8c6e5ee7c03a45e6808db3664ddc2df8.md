### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Inflates Bridged Amount, Breaking EVM Collateralization - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` uses `safeTransferFrom` to pull `amount` tokens from the caller, then unconditionally emits an `InitTransfer` event with that same `amount`. For fee-on-transfer ERC20 tokens, the bridge actually receives `amount - transfer_fee` tokens, but the event — and therefore the cross-chain proof — records the full `amount`. NEAR's bridge finalizes the transfer based on the event's `amount`, minting or releasing more tokens than the EVM bridge actually holds, permanently undercollateralizing the bridge.

---

### Finding Description

In `OmniBridge.initTransfer`, the non-bridge-token path pulls tokens from the caller:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // requested amount
);
```

Immediately after, the event is emitted with the caller-supplied `amount`:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,         // ← not the actual received amount
    fee,
    nativeFee,
    recipient,
    message
);
``` [1](#0-0) [2](#0-1) 

The NEAR prover parses this event log and constructs an `InitTransferMessage` whose `amount` field is taken directly from the event:

```rust
amount: near_sdk::json_types::U128(event.data.amount),
``` [3](#0-2) 

NEAR's `fin_transfer_callback` then denormalizes and uses this `amount` to compute `amount_without_fee` and send tokens to the recipient:

```rust
amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
``` [4](#0-3) 

```rust
self.send_tokens(
    token.clone(),
    recipient,
    U128(transfer_message.amount_without_fee()...),
    &msg,
)
``` [5](#0-4) 

No balance-before/balance-after check is performed on the EVM side to verify how many tokens were actually received. The same pattern exists in the StarkNet bridge:

```cairo
let success = IERC20Dispatcher { contract_address: token_address }
    .transfer_from(caller, get_contract_address(), amount.into());
// event emits `amount`, not actual received
``` [6](#0-5) [7](#0-6) 

---

### Impact Explanation

**High — Balance/accounting corruption that breaks bridge collateralization.**

Concrete scenario:
1. A fee-on-transfer ERC20 token charges 5% on every transfer.
2. User calls `initTransfer(token, amount=1000, fee=10, ...)`.
3. `safeTransferFrom` executes; bridge receives **950** tokens (1000 − 5%).
4. `InitTransfer` event records `amount=1000`.
5. NEAR prover verifies the event and creates a transfer for 1000 units.
6. NEAR bridge releases `1000 − 10 = 990` tokens to the recipient.
7. EVM bridge is now **undercollateralized by 40 tokens** (holds 950, owes 990).

Repeated over many transfers, the EVM bridge's token reserves are drained below the total outstanding NEAR-side supply. When users attempt to bridge back from NEAR to EVM, the EVM bridge cannot fulfill withdrawals, permanently freezing or stealing funds from later users.

---

### Likelihood Explanation

Fee-on-transfer ERC20 tokens (e.g., USDT on some chains, deflationary tokens, reflection tokens) are common in production. Any unprivileged user can call `initTransfer` with such a token — no special role or access is required. The bridge does not whitelist tokens, so any ERC20 that passes the `!isBridgeToken` check is eligible. The vulnerability is triggered on every such transfer.

---

### Recommendation

Record the bridge's token balance before and after the `safeTransferFrom` call, and use the difference as the canonical `amount` for the event:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(actualReceived > fee, "InvalidFee");
// use actualReceived in the event and extension call, not amount
```

Apply the same fix to `starknet/src/omni_bridge.cairo`'s `init_transfer` and to the Solana `init_transfer` when Token-2022 transfer-fee extension tokens are used.

---

### Proof of Concept

1. Deploy a standard ERC20 with a 5% fee-on-transfer hook.
2. Register it with the bridge (via `logMetadata` + NEAR-side token registration).
3. Approve the bridge for 1000 tokens and call:
   ```solidity
   bridge.initTransfer(feeToken, 1000, 10, 0, "near-recipient.near", "");
   ```
4. Observe: bridge's `feeToken` balance increases by only 950.
5. Observe: `InitTransfer` event emits `amount=1000`.
6. Submit the event proof to NEAR `fin_transfer`; NEAR releases 990 tokens to the recipient.
7. EVM bridge is undercollateralized by 40 tokens per transfer. [8](#0-7)

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

**File:** near/omni-types/src/evm/events.rs (L126-126)
```rust
            amount: near_sdk::json_types::U128(event.data.amount),
```

**File:** near/omni-bridge/src/lib.rs (L725-725)
```rust
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
```

**File:** near/omni-bridge/src/lib.rs (L1957-1966)
```rust
        self.send_tokens(
            token.clone(),
            recipient,
            U128(
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            ),
            &msg,
        )
```

**File:** starknet/src/omni_bridge.cairo (L304-306)
```text
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
```

**File:** starknet/src/omni_bridge.cairo (L316-329)
```text
            self
                .emit(
                    Event::InitTransfer(
                        InitTransfer {
                            sender: caller,
                            token_address,
                            origin_nonce,
                            amount,
                            fee,
                            native_fee,
                            recipient,
                            message,
                        },
                    ),
```
