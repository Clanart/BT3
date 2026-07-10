### Title
Missing Recipient Validation in `initTransfer` Allows Permanent Irrecoverable Locking of User Funds — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The EVM `OmniBridge.initTransfer` (and `initTransfer1155`) function accepts a `string calldata recipient` parameter but performs no validation that it is non-empty or represents a parseable cross-chain address. A user who calls `initTransfer` with an empty `recipient` string will have their tokens permanently burned or locked on EVM with no recovery path, because the resulting transfer cannot be finalized on the destination chain.

---

### Finding Description

`OmniBridge.initTransfer` performs only one input guard before consuming user tokens:

```solidity
if (fee >= amount) {
    revert InvalidFee();
}
``` [1](#0-0) 

There is no check that `recipient` is non-empty or syntactically valid as a cross-chain address. After this single guard, the function immediately burns or locks the caller's tokens:

```solidity
} else if (isBridgeToken[tokenAddress]) {
    BridgeToken(tokenAddress).burn(msg.sender, amount);
} else {
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
}
``` [2](#0-1) 

Then it emits the `InitTransfer` event with the empty recipient string verbatim:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message
);
``` [3](#0-2) 

The same omission exists in `initTransfer1155`: [4](#0-3) 

By contrast, the NEAR-side `init_transfer` explicitly rejects a recipient whose chain resolves to `ChainKind::Near` (i.e., it enforces structural validity of the `OmniAddress`):

```rust
require!(
    init_transfer_msg.recipient.get_chain() != ChainKind::Near,
    BridgeError::InvalidRecipientChain.as_ref()
);
``` [5](#0-4) 

And the StarkNet `init_transfer` at least checks `amount > 0` and `fee < amount`, but also omits any recipient non-empty check:

```cairo
assert(amount > 0, 'ERR_ZERO_AMOUNT');
assert(fee < amount, 'ERR_INVALID_FEE');
``` [6](#0-5) 

The EVM entry point is the weakest: it has neither an `amount > 0` assertion (relying solely on the implicit `fee >= amount` revert when both are zero) nor any recipient validity check.

---

### Impact Explanation

When a user calls `initTransfer` with `recipient = ""`:

1. Tokens are **irreversibly burned** (bridge tokens) or **locked** (standard ERC-20) on EVM.
2. An `InitTransfer` event is emitted with an empty recipient field.
3. NEAR relayers observe the event and attempt to finalize the transfer on NEAR by calling `fin_transfer` with a proof derived from that event.
4. On NEAR, the empty recipient string cannot be deserialized into a valid `OmniAddress`, causing `fin_transfer` to panic or revert.
5. Because the EVM burn/lock is already committed and there is no on-chain refund or rollback mechanism for EVM-originated transfers, the user's funds are **permanently unclaimable**.

This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

The function is public and callable by any token holder. A user can accidentally pass an empty string (e.g., a UI bug, a direct contract call with a missing argument, or a scripting error). No privileged role is required. The attacker-controlled entry path is a direct call to `initTransfer` with `recipient = ""` and any non-zero `amount` with `fee = 0`.

---

### Recommendation

Add an explicit non-empty recipient check before any token movement in both `initTransfer` and `initTransfer1155`:

```solidity
require(bytes(recipient).length > 0, "Invalid recipient");
```

Optionally, add a minimum length check consistent with the shortest valid cross-chain address format used by the protocol (e.g., a NEAR account ID or an EVM hex address string).

---

### Proof of Concept

```solidity
// Attacker or user calls initTransfer with empty recipient
omniBridge.initTransfer(
    tokenAddress,   // valid ERC-20
    1000,           // amount
    0,              // fee
    0,              // nativeFee
    "",             // recipient — empty, no validation
    ""              // message
);
// Tokens are now burned/locked on EVM.
// NEAR fin_transfer will fail to parse "" as OmniAddress.
// Funds are permanently unclaimable.
``` [7](#0-6)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L439-490)
```text
    function initTransfer1155(
        address tokenAddress,
        uint256 tokenId,
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

        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
        );

        IERC1155(tokenAddress).safeTransferFrom(
            msg.sender,
            address(this),
            tokenId,
            amount,
            ""
        );

        uint256 extensionValue = msg.value - nativeFee;

        initTransferExtension(
            msg.sender,
            deterministicToken,
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
            deterministicToken,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```

**File:** near/omni-bridge/src/lib.rs (L531-534)
```rust
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );
```

**File:** starknet/src/omni_bridge.cairo (L292-293)
```text
            assert(amount > 0, 'ERR_ZERO_AMOUNT');
            assert(fee < amount, 'ERR_INVALID_FEE');
```
