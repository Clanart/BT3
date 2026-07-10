### Title
Fee-on-Transfer Token Accounting Discrepancy in `initTransfer` Breaks Bridge Collateralization - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge::initTransfer` calls `safeTransferFrom(msg.sender, address(this), amount)` but emits the user-supplied `amount` in the `InitTransfer` event without verifying the actual balance received. For fee-on-transfer tokens, the bridge receives `amount - transfer_fee` but the cross-chain event records `amount`. The NEAR side treats the event as the sole authoritative proof of locked collateral and releases the full `amount` to the recipient, permanently undercollateralizing the bridge. The same flaw exists identically in `starknet/src/omni_bridge.cairo::init_transfer`.

---

### Finding Description

In `OmniBridge::initTransfer`, the non-bridge-token path performs:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // user-supplied parameter
);
```

Immediately after, the function emits:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,         // same user-supplied parameter, NOT actual received balance
    fee,
    nativeFee,
    recipient,
    message
);
``` [1](#0-0) [2](#0-1) 

The project's own security documentation explicitly states: *"The NEAR side relies solely on these events — any missing or ambiguous field means lost funds or spoofable transfers"* and *"the NEAR side will treat any emitted event as proof that tokens are held."* [3](#0-2) 

The `InitTransfer` event is decoded on the NEAR side via `near/omni-types/src/evm/events.rs`, where the `amount` field is taken directly from the event log and used to determine how many tokens to release to the recipient. [4](#0-3) 

No balance snapshot is taken before or after the `safeTransferFrom` call to verify the actual amount received. For a fee-on-transfer token (e.g., a reflection token that deducts a percentage on every transfer), the bridge receives `amount - fee_deducted` but the cross-chain message claims `amount` was locked.

The identical pattern exists in StarkNet:

```cairo
let success = IERC20Dispatcher { contract_address: token_address }
    .transfer_from(caller, get_contract_address(), amount.into());
assert(success, 'ERR_TRANSFER_FROM_FAILED');
// ...
emit InitTransfer { ..., amount, ... }  // user-supplied amount, not actual received
``` [5](#0-4) 

---

### Impact Explanation

Every time a user bridges a fee-on-transfer token EVM→NEAR (or StarkNet→NEAR), the EVM bridge locks `amount - fee_deducted` but the NEAR side releases `amount`. The bridge is undercollateralized by `fee_deducted` per transfer. Over repeated transfers, the cumulative shortfall grows. When users attempt to bridge back NEAR→EVM, the EVM bridge cannot release the full claimed amount for the last users, resulting in **permanent irrecoverable lock of user funds** in the bridge vault.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows"* and *"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value."*

---

### Likelihood Explanation

Fee-on-transfer tokens are a well-known ERC20 class (reflection tokens, deflationary tokens). Any such token that is registered on the bridge (i.e., has a `token_decimals` entry on NEAR and a corresponding factory mapping) is immediately exploitable by any unprivileged user calling `initTransfer`. No privileged access, leaked keys, or colluding operators are required. The attacker simply calls `initTransfer` with the fee-on-transfer token and a valid `amount`. The discrepancy accumulates silently with every transfer until the bridge is drained.

---

### Recommendation

Capture the bridge contract's token balance before and after the `safeTransferFrom` call, and use the **actual received amount** (the balance delta) as the value recorded in the `InitTransfer` event:

```solidity
} else {
    uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    uint256 balanceAfter = IERC20(tokenAddress).balanceOf(address(this));
    uint128 actualReceived = uint128(balanceAfter - balanceBefore);
    amount = actualReceived; // use actual received amount for the event
}
```

Apply the same fix to `starknet/src/omni_bridge.cairo::init_transfer` by reading `balance_of` before and after `transfer_from` and emitting the delta as `amount`.

---

### Proof of Concept

1. Deploy a fee-on-transfer ERC20 token `FeeToken` that deducts 10% on every `transferFrom`.
2. Register `FeeToken` on the EVM bridge and NEAR bridge (add `token_decimals` entry, factory mapping).
3. User calls `OmniBridge::initTransfer(FeeToken, 1000, 0, 0, "user.near", "")`.
4. `safeTransferFrom` transfers 1000 tokens from user; bridge receives 900 (10% fee deducted).
5. `InitTransfer` event emits `amount = 1000`.
6. NEAR relayer reads the event, verifies the MPC signature over the payload containing `amount = 1000`, and releases 1000 tokens to `user.near`.
7. Bridge EVM vault holds only 900 tokens but has issued a claim for 1000.
8. Repeat 10 times: bridge holds 9000 tokens but has issued claims for 10000.
9. The 10th user to bridge back NEAR→EVM calls `finTransfer` for 1000 tokens; the bridge only has 0 tokens left → `safeTransfer` reverts → **permanent lock of 1000 tokens**. [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L406-436)
```text
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

**File:** evm/CLAUDE.md (L23-36)
```markdown
**EVM → NEAR (initTransfer)**: User calls `initTransfer` which burns/locks tokens on EVM and emits `InitTransfer` with all transfer details (sender, token, amount, fee, nativeFee, recipient, message). In the Wormhole variant, a Wormhole message is also sent. The NEAR side reads this event (via light client or Wormhole) to complete the transfer. Every field needed to reconstruct the transfer must be in the event — it is the only data the NEAR side sees.

## Custom Token Support

Tokens with non-standard mint/burn (e.g. eNEAR) are supported via `ICustomMinter` (src/common/ICustomMinter.sol) and registered through `addCustomToken()`. See `ENearProxy` (src/eNear/contracts/ENearProxy.sol) for the eNEAR implementation.

## Security

### Invariants
- **No replay attacks**: Every `destinationNonce` must be checked against `completedTransfers` and marked used before any token transfer. Every `originNonce` is incremented atomically. A nonce must never be reusable
- **Event completeness**: `InitTransfer` and `FinTransfer` events must contain every field needed to reconstruct the transfer. The NEAR side relies solely on these events — any missing or ambiguous field means lost funds or spoofable transfers. Fields must not be collapsible (e.g. two different transfers must never produce the same event data)
- **State before external calls**: Always mutate state (e.g. mark nonce used) before any external call (token transfer, ETH send, custom minter). This is the primary reentrancy defense
- **No token release without signature**: Never mint, transfer, or unlock tokens to a recipient without first verifying a valid MPC signature. No admin function, emergency path, or refactor may bypass this — it is the only authorization gate for finTransfer
- **Event–transfer atomicity**: `InitTransfer` must only be emitted in a code path where tokens have already been burned/locked in the same transaction. If the token transfer reverts or is skipped, the event must not emit — the NEAR side will treat any emitted event as proof that tokens are held
```

**File:** near/omni-types/src/evm/events.rs (L11-21)
```rust
sol! {
    event InitTransfer(
        address indexed sender,
        address indexed tokenAddress,
        uint64 indexed originNonce,
        uint128 amount,
        uint128 fee,
        uint128 nativeTokenFee,
        string recipient,
        string message
    );
```

**File:** starknet/src/omni_bridge.cairo (L303-330)
```text
            } else {
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
            }

            if native_fee > 0 {
                let native_token = self.strk_token_address.read();
                let success = IERC20Dispatcher { contract_address: native_token }
                    .transfer_from(caller, get_contract_address(), native_fee.into());
                assert(success, 'ERR_FEE_TRANSFER_FAILED');
            }

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
