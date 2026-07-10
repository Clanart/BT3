### Title
Deflationary Token Accounting Mismatch Causes Bridge Undercollateralization and Permanent Fund Lock - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.sol`'s `initTransfer` uses the caller-supplied `amount` parameter both for the `safeTransferFrom` call and for the emitted `InitTransfer` event. For deflationary (fee-on-transfer) ERC20 tokens, the bridge receives strictly less than `amount`, but the cross-chain message records the full nominal `amount`. This creates a growing collateral deficit: early withdrawers receive their full entitlement while later withdrawers find the bridge insolvent, permanently locking their funds.

---

### Finding Description

In `OmniBridge.sol`, the `initTransfer` function handles native (non-bridge, non-custom-minter) ERC20 tokens with:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // ← nominal amount, not actual received
);
``` [1](#0-0) 

Immediately after, the event is emitted with the same nominal `amount`:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,          // ← recorded as if full amount was received
    ...
);
``` [2](#0-1) 

There is no balance-before/balance-after check to determine the actual tokens received. For a deflationary token that deducts a transfer fee (e.g., 1%), a deposit of 100 tokens results in the bridge holding 99 but recording 100 in the cross-chain message.

The NEAR side reads the `InitTransfer` event and mints/releases the full recorded `amount` to the recipient. When those recipients later bridge back, NEAR burns the full `amount` and issues a `FinTransfer` message to EVM for the full `amount`. The EVM `finTransfer` then attempts:

```solidity
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount   // ← full nominal amount, bridge may not hold this
);
``` [3](#0-2) 

With N depositors each depositing 100 deflationary tokens (1% fee), the bridge holds `99N` but owes `100N`. The first `floor(99N/100)` withdrawers succeed; the remaining withdrawers find the bridge insolvent and their `finTransfer` calls revert permanently.

The identical pattern exists in StarkNet's `omni_bridge.cairo`:

```cairo
let success = IERC20Dispatcher { contract_address: token_address }
    .transfer_from(caller, get_contract_address(), amount.into());
// event emits `amount`, not actual received
``` [4](#0-3) [5](#0-4) 

---

### Impact Explanation

**Critical / High** — Two overlapping impacts:

1. **Permanent fund lock**: The last depositor(s) to withdraw cannot complete `finTransfer` because the bridge's actual token balance is less than the sum of all recorded obligations. Their NEAR-side wrapped tokens have already been burned; the EVM-side `safeTransfer` reverts. Funds are irrecoverably locked.

2. **Bridge collateralization corruption**: Every deflationary-token deposit silently widens the gap between recorded obligations and actual holdings. The bridge's accounting is structurally broken for any such token, matching the "balance/accounting corruption that breaks bridge collateralization" impact class.

---

### Likelihood Explanation

**Medium-High.** Any unprivileged user can call `initTransfer` with a deflationary ERC20 token. No special role, leaked key, or operator collusion is required. Deflationary tokens (e.g., REFLECT-style, STA, DEFX) are a well-known token class. The bridge does not whitelist tokens, so any user can trigger this path. The deficit accumulates silently with each deposit and only manifests at withdrawal time, making it hard to detect before funds are locked.

---

### Recommendation

Replace the nominal-amount pattern with a balance-before/after check to determine the actual received amount, and use that value in the emitted event and cross-chain message:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 actualReceived = IERC20(tokenAddress).balanceOf(address(this)) - balanceBefore;
// Use actualReceived (cast to uint128) in the event and cross-chain payload
```

Apply the same fix to StarkNet's `init_transfer` in `omni_bridge.cairo`. Alternatively, document that deflationary tokens are explicitly unsupported and add a token allowlist enforced on-chain.

---

### Proof of Concept

**Setup**: Deploy a deflationary ERC20 token that deducts 1% on every `transferFrom` (as described in the referenced gist). Register it with the EVM OmniBridge (no whitelist check prevents this).

**Steps**:

1. Alice calls `initTransfer(deflToken, 100, 0, 0, "near:alice.near", "")` → bridge receives 99, emits `InitTransfer(amount=100)` → NEAR mints 100 wrapped tokens to Alice.
2. Bob calls `initTransfer(deflToken, 100, 0, 0, "near:bob.near", "")` → bridge receives 99, emits `InitTransfer(amount=100)` → NEAR mints 100 to Bob.
3. Eve calls `initTransfer(deflToken, 100, 0, 0, "near:eve.near", "")` → bridge receives 99, emits `InitTransfer(amount=100)` → NEAR mints 100 to Eve.
4. Bridge holds **297** deflToken, owes **300**.
5. Alice bridges back 100 wrapped tokens → NEAR burns 100, MPC signs `FinTransfer(amount=100)` → EVM `safeTransfer(alice, 100)` succeeds. Bridge holds 197.
6. Bob bridges back 100 → EVM `safeTransfer(bob, 100)` succeeds. Bridge holds 97.
7. Eve bridges back 100 → EVM `safeTransfer(eve, 100)` **reverts** (bridge only holds 97). Eve's NEAR-side tokens are already burned. **Eve's 100 tokens are permanently lost.**

The root cause — recording `amount` instead of `actualReceived` — is entirely within `OmniBridge.sol` lines 407–411 and 427–436, with no external dependency required. [6](#0-5) [2](#0-1) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L351-354)
```text
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
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

**File:** starknet/src/omni_bridge.cairo (L303-307)
```text
            } else {
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
            }
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
