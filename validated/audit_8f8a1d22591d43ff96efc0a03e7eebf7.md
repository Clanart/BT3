### Title
Burn-on-Transfer Token Accounting Inflation Enables Bridge Undercollateralization and Permanent Fund Freeze - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
In `OmniBridge.initTransfer`, when a native (non-bridge, non-custom-minter) ERC20 token with a burn-on-transfer (deflationary) mechanic is deposited, the contract records and broadcasts the caller-supplied `amount` rather than the actual tokens received. The Wormhole message carries the inflated figure, causing NEAR to mint or release more tokens than the EVM vault holds. Over time the vault becomes undercollateralized, and later `finTransfer` calls for that token will revert for lack of funds, permanently freezing the affected users' assets.

### Finding Description

`OmniBridge.initTransfer` handles three token classes. For tokens that are neither a bridge-deployed token nor a custom-minter token, it executes a plain `safeTransferFrom`:

```solidity
// OmniBridge.sol lines 406-411
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount          // ← caller-supplied, not verified against actual receipt
    );
}
```

Immediately after, the same unverified `amount` is forwarded to `initTransferExtension`:

```solidity
// OmniBridge.sol lines 415-425
initTransferExtension(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // ← still the caller-supplied figure
    fee,
    nativeFee,
    recipient,
    message,
    extensionValue
);
```

In `OmniBridgeWormhole.initTransferExtension`, that same `amount` is serialized into the Wormhole payload and published:

```solidity
// OmniBridgeWormhole.sol lines 129-148
bytes memory payload = bytes.concat(
    ...
    Borsh.encodeUint128(amount),   // ← inflated amount broadcast cross-chain
    ...
);
_wormhole.publishMessage{value: value}(wormholeNonce, payload, _consistencyLevel);
```

For a burn-on-transfer token (e.g., one that burns 1% on every transfer), a call with `amount = 1000` causes the vault to receive only 990 tokens, yet the Wormhole message asserts 1000. NEAR processes the message at face value and mints or releases 1000 tokens to the recipient. The EVM vault is now short by 10 tokens per such transfer. Each subsequent deposit compounds the deficit.

When any user later bridges back from NEAR to EVM, `finTransfer` attempts:

```solidity
// OmniBridge.sol lines 351-354
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount   // ← amount the vault cannot fully cover
);
```

Once the cumulative shortfall exceeds the vault balance, this transfer reverts. The affected users' funds are permanently locked on NEAR with no recourse.

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

The vault becomes structurally undercollateralized for the affected token. Every `finTransfer` that would drain the vault below zero reverts, leaving those users unable to claim their assets on either chain. The deficit grows monotonically with each new deposit of the deflationary token; no privileged action can recover the missing collateral without an out-of-band top-up.

### Likelihood Explanation

**Medium.** The entry path requires only that a burn-on-transfer ERC20 token be registered (or self-registered via `logMetadata`) and that a user calls `initTransfer` with it. No privileged access is needed. Deflationary tokens are a well-known token class (e.g., DEFX, SAFEMOON-style tokens). The bridge imposes no whitelist on which ERC20 tokens can be deposited via the native path, so any user can trigger this condition.

### Recommendation

Before emitting the Wormhole message, measure the actual tokens received by comparing the vault balance before and after the transfer, and use that delta as the authoritative amount:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(uint128(actualReceived) == actualReceived, "amount overflow");
amount = uint128(actualReceived);
```

Pass `amount` (now the actual received value) to `initTransferExtension`. Alternatively, document that deflationary tokens are unsupported and add an explicit on-chain check or registry that rejects them.

### Proof of Concept

1. Deploy or identify a burn-on-transfer ERC20 token `T` that burns 10% on every transfer.
2. Call `OmniBridge.logMetadata(address(T))` to register it (no privilege required).
3. Call `OmniBridge.initTransfer(address(T), 1000, 0, nativeFee, nearRecipient, "")` with `msg.value = nativeFee`.
   - `safeTransferFrom` moves 1000 T from the caller; the token burns 100, so the vault receives 900.
   - The Wormhole message encodes `amount = 1000`.
4. NEAR processes the Wormhole VAA and releases/mints 1000 T-equivalent tokens to `nearRecipient`.
5. Repeat step 3 ten times. The vault holds 9000 T but NEAR has issued 10000 T-equivalent claims.
6. Any user who now bridges 9001 T-equivalent back to EVM triggers `finTransfer` with `amount = 9001`; `safeTransfer` reverts because the vault only holds 9000 T. Those users' funds are permanently frozen. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L351-355)
```text
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L415-425)
```text
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L129-148)
```text
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.InitTransfer)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(sender),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeUint64(originNonce),
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
            Borsh.encodeUint128(nativeFee),
            Borsh.encodeString(recipient),
            Borsh.encodeString(message)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

```
