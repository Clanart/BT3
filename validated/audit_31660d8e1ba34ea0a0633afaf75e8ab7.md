### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Causes Unbacked Minted Supply — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` calls `safeTransferFrom` with the caller-supplied `amount` and then emits `InitTransfer` with that same `amount`. For fee-on-transfer ERC-20 tokens the vault receives strictly less than `amount`, but the NEAR bridge reads the emitted event and mints the full `amount` of bridged tokens on NEAR. The result is unbacked supply on NEAR and a collateral shortfall on EVM that permanently locks funds for later redeemers.

---

### Finding Description

In `OmniBridge.initTransfer`, when the token is neither a bridge token nor a custom-minter token, the contract pulls tokens from the caller:

```solidity
// OmniBridge.sol lines 407-411
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // caller-supplied, not balance-checked
);
```

Immediately after, the event is emitted using the same caller-supplied `amount`:

```solidity
// OmniBridge.sol lines 427-436
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,         // recorded as-is, no balance-diff check
    fee,
    nativeFee,
    recipient,
    message
);
```

No pre/post balance snapshot is taken. For a fee-on-transfer token the vault receives `amount - token_fee`, but the event asserts `amount` was locked. The NEAR bridge consumes this event and mints `amount` of bridged tokens to the recipient, creating supply that exceeds the actual collateral held on EVM.

The identical pattern exists in `starknet/src/omni_bridge.cairo` `init_transfer` (lines 304–306 and 323), where `transfer_from` is called with `amount` and the `InitTransfer` event records `amount` without a balance-difference check.

---

### Impact Explanation

**Critical / High — unbacked minted supply + permanent fund lock.**

- NEAR mints `amount` bridged tokens; EVM vault holds only `amount - token_fee`. Every such deposit widens the collateral gap.
- When users later bridge back (NEAR → EVM), `finTransfer` calls `safeTransfer(recipient, payload.amount)` (line 351–354). Once cumulative shortfall exceeds the vault balance, the transfer reverts. Funds of later redeemers are permanently locked in the bridge.
- An attacker who controls or deploys a fee-on-transfer token that is registered as a native (non-bridge) token can deliberately inflate the gap, draining the vault for other users.

This matches two allowed impacts:
- **Critical**: unauthorized mint of bridged assets (NEAR mints more than locked on EVM).
- **Critical**: irrecoverable lock of user funds in the bridge vault.

---

### Likelihood Explanation

Fee-on-transfer tokens (e.g., USDT on some chains, STA, PAXG, tokens with configurable fees) are common. Any such token that is registered as a native (non-`isBridgeToken`, non-`customMinters`) token in the bridge is exploitable by any unprivileged user simply by calling `initTransfer`. No special role or key is required; the entry point is fully public.

---

### Recommendation

Measure the actual balance received using a pre/post balance snapshot and use the difference as the canonical locked amount:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
require(uint128(actualReceived) == actualReceived, "overflow");
amount = uint128(actualReceived);   // use actual received for event + accounting
```

Apply the same fix to `starknet/src/omni_bridge.cairo` `init_transfer`.

---

### Proof of Concept

1. Deploy or use an existing fee-on-transfer ERC-20 token `T` with a 1% transfer fee. Register `T` as a native token in `OmniBridge` (not `isBridgeToken`, no `customMinters` entry).
2. Call `OmniBridge.initTransfer(T, 1_000_000, 0, 0, "alice.near", "")`.
3. `safeTransferFrom` transfers 1,000,000 units; the token's fee mechanism retains 10,000, so the vault receives 990,000.
4. The emitted `InitTransfer` event records `amount = 1,000,000`.
5. The NEAR bridge relayer reads the event and mints 1,000,000 bridged-T to `alice.near`.
6. Repeat N times. The vault is short by `N × 10,000` units.
7. When later users bridge back from NEAR, `finTransfer` calls `safeTransfer(recipient, 1_000_000)`. Once the vault balance falls below 1,000,000, the call reverts — those users' funds are permanently locked.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L351-354)
```text
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L407-411)
```text
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
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

**File:** starknet/src/omni_bridge.cairo (L304-306)
```text
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
```

**File:** starknet/src/omni_bridge.cairo (L316-330)
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
                )
```
