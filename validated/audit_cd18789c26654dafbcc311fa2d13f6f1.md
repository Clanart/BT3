### Title
EVM `initTransfer` Records User-Specified `amount` Instead of Actual Received Amount, Enabling Bridge Undercollateralization via Fee-on-Transfer Tokens — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `initTransfer` function in `OmniBridge.sol` calls `safeTransferFrom` to pull tokens from the user but emits the user-supplied `amount` parameter in the `InitTransfer` event rather than the actual amount received by the contract. For fee-on-transfer ERC20 tokens, the actual received amount is strictly less than `amount`. The NEAR bridge's `fin_transfer_callback` processes the proof of this event and records the inflated `amount` as locked, causing the bridge to mint or release more tokens on NEAR than the EVM bridge actually holds. This breaks bridge collateralization and can permanently freeze funds for other users.

---

### Finding Description

In `OmniBridge.sol`, the `initTransfer` function handles non-bridge-token ERC20 deposits as follows:

```solidity
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount          // ← user-supplied input
    );
}
```

After the transfer, the function emits:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,             // ← same user-supplied input, NOT actual received
    fee,
    nativeFee,
    recipient,
    message
);
```

`SafeERC20.safeTransferFrom` does not return the actual amount received; it only verifies that `transferFrom` returned `true`. For fee-on-transfer tokens, the contract receives `amount - transfer_fee` tokens, but the event records `amount`. The NEAR bridge's `fin_transfer_callback` then deserialises the proof of this event and constructs a `TransferMessage` using the emitted (inflated) amount:

```rust
let transfer_message = TransferMessage {
    ...
    amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
    ...
};
```

This is the direct analog of the original report's bug: the inner call's actual result (real tokens received) is discarded, and the input value (`amount`) is used for all downstream accounting.

---

### Impact Explanation

**Severity: High — accounting corruption that breaks bridge collateralization.**

- The EVM bridge holds `amount - transfer_fee` tokens but the NEAR bridge records `amount` as locked.
- The NEAR bridge mints `amount` wrapped tokens to the recipient.
- When the recipient bridges back, the NEAR bridge burns `amount` wrapped tokens and the EVM bridge attempts to release `amount` tokens via `safeTransfer`. The EVM bridge only holds `amount - transfer_fee`, so the `safeTransfer` reverts.
- The recipient's funds are permanently frozen on the EVM side.
- Repeated transfers drain the EVM bridge's reserves, eventually freezing all subsequent users' withdrawals.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows"* and *"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value."*

---

### Likelihood Explanation

The `initTransfer` `else` branch accepts any ERC20 token address that is neither a bridge token nor a custom-minter token. For the NEAR bridge to process the proof, the token must be registered in the NEAR bridge's token mapping (managed by the DAO). The DAO may unknowingly register a fee-on-transfer token (e.g., tokens with deflationary mechanics, reflection tokens, or tokens with configurable fees). This does not require malicious operator behavior — only an oversight during token registration. Fee-on-transfer tokens are a known class of ERC20 tokens in production use.

---

### Recommendation

Measure the actual received amount by comparing balances before and after the transfer, and use that value in the event:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint128 actualReceived = uint128(
    IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore
);
// Use actualReceived in the event and all downstream accounting
emit BridgeTypes.InitTransfer(
    msg.sender, tokenAddress, currentOriginNonce,
    actualReceived, fee, nativeFee, recipient, message
);
```

Alternatively, explicitly reject fee-on-transfer tokens by requiring `actualReceived == amount`.

---

### Proof of Concept

**Setup:** A fee-on-transfer ERC20 token `FeeToken` (1% transfer fee) is registered in the NEAR bridge's token mapping.

1. Alice calls `initTransfer(FeeToken, 1000, 0, 0, "alice.near", "")` on the EVM bridge.
2. `safeTransferFrom` transfers 1000 tokens from Alice; the EVM bridge receives **990** (1% fee deducted by the token contract).
3. The `InitTransfer` event emits `amount = 1000` (the user-supplied input, not 990).
4. The NEAR bridge's `fin_transfer_callback` processes the proof: `transfer_message.amount = denormalize(1000)`.
5. NEAR bridge mints **1000** wrapped `FeeToken` to Alice on NEAR.
6. Alice bridges back 1000 wrapped tokens: NEAR bridge burns 1000, EVM bridge calls `safeTransfer(Alice, 1000)`.
7. EVM bridge only holds **990** tokens → `safeTransfer` reverts → Alice's 1000 wrapped tokens are burned but she receives nothing on EVM.
8. Alice loses 1000 tokens; the bridge is now short 10 tokens per round-trip, progressively draining collateral.

**Root cause location:** [1](#0-0) 

**Event emission using input `amount` instead of actual received:** [2](#0-1) 

**NEAR callback consuming the inflated amount from the event proof:** [3](#0-2) 

**EVM `finTransfer` releasing tokens (will revert if bridge is undercollateralized):** [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L350-355)
```text
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L406-412)
```text
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L427-436)
```text
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

**File:** near/omni-bridge/src/lib.rs (L722-732)
```rust
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
```
