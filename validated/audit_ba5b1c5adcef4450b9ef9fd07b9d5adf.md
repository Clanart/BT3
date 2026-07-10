The core vulnerability is clear from the `OmniBridge.sol` code. Let me confirm the NEAR-side processing to complete the impact chain.

### Title
Fee-on-Transfer Token Accounting Discrepancy in `initTransfer` Allows Bridge Undercollateralization - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` uses the caller-supplied `amount` parameter directly in the emitted `InitTransfer` event and Wormhole message after calling `safeTransferFrom`. For fee-on-transfer ERC20 tokens, the bridge receives fewer tokens than `amount`, but the cross-chain message records the full `amount`. The NEAR side then releases `amount` tokens to the recipient, creating unbacked supply and draining the bridge's collateral reserves.

---

### Finding Description

In `OmniBridge.sol::initTransfer`, the else-branch for standard ERC20 tokens performs:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount          // ← user-supplied parameter
);
``` [1](#0-0) 

Immediately after, the function emits the cross-chain event with the same `amount`:

```solidity
emit BridgeTypes.InitTransfer(
    msg.sender,
    tokenAddress,
    currentOriginNonce,
    amount,   // ← not the actual received amount
    fee,
    nativeFee,
    recipient,
    message
);
``` [2](#0-1) 

In `OmniBridgeWormhole.sol::initTransferExtension`, the same `amount` is encoded into the Wormhole message payload:

```solidity
Borsh.encodeUint128(amount),
``` [3](#0-2) 

There is no balance-difference check (i.e., no `balanceBefore`/`balanceAfter` measurement) to determine the actual tokens received. For fee-on-transfer tokens, the bridge receives `amount - transferFee` but records `amount` in the cross-chain message.

On the NEAR side, `fin_transfer_callback` reads `init_transfer.amount` directly from the prover result (which is derived from the EVM event) and uses it to release tokens to the recipient:

```rust
amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
``` [4](#0-3) 

No correction for the actual received amount is applied at any point in the NEAR processing path.

---

### Impact Explanation

For every `initTransfer` call with a fee-on-transfer token, the bridge releases `amount` on NEAR but only holds `amount - transferFee` on EVM. The bridge becomes progressively undercollateralized. Users bridging back from NEAR to EVM will eventually find insufficient locked tokens, causing their withdrawals to fail permanently — a direct loss of user funds. An attacker who controls or uses a fee-on-transfer token registered with the bridge can systematically drain the bridge's ERC20 reserves.

This matches the allowed impact: **Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value.**

---

### Likelihood Explanation

Several ERC20 tokens in production have transfer fees (e.g., STA, PAXG, tokens with configurable fee switches). If any such token is registered with the bridge via the standard ERC20 path (not `isBridgeToken`, not `customMinters`), the vulnerability is immediately exploitable by any token holder calling `initTransfer`. The attacker needs no privileged access — only a balance of the fee-on-transfer token and knowledge of the bridge interface.

---

### Recommendation

Measure the actual received amount using a balance-difference pattern:

```solidity
uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
uint256 balanceAfter = IERC20(tokenAddress).balanceOf(address(this));
uint128 actualReceived = uint128(balanceAfter - balanceBefore);
```

Use `actualReceived` (not `amount`) in all downstream logic: the `InitTransfer` event, the Wormhole message payload, and the `initTransferExtension` call. This mirrors the fix recommended in the reference report (using balance difference around the transfer).

---

### Proof of Concept

1. A fee-on-transfer token `FOT` (2% fee on every transfer) is registered with the bridge (not as a bridge token, not as a custom minter).
2. Attacker calls `initTransfer(FOT, 1_000_000, 0, 0, "near:attacker.near", "")`.
3. Bridge receives `980_000` FOT (`1_000_000 - 2%`), but emits `InitTransfer` with `amount = 1_000_000`.
4. NEAR relayer picks up the event, submits proof to NEAR bridge.
5. `fin_transfer_callback` on NEAR reads `amount = 1_000_000` from the proof and releases `1_000_000` FOT-equivalent tokens to `attacker.near`.
6. Attacker gained `20_000` tokens of unbacked value per call.
7. Repeated calls drain the bridge's FOT reserves; legitimate users bridging FOT back to EVM receive `ERR_INSUFFICIENT_LOCKED_TOKENS` panics from `unlock_tokens`. [5](#0-4)

### Citations

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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L136-136)
```text
            Borsh.encodeUint128(amount),
```

**File:** near/omni-bridge/src/lib.rs (L725-725)
```rust
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
```

**File:** near/omni-bridge/src/token_lock.rs (L81-84)
```rust
        require!(
            available >= amount,
            TokenLockError::InsufficientLockedTokens.as_ref()
        );
```
