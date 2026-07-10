### Title
Unguarded `abi.decode` on User-Controlled Data in `coreReceiveWithData` Causes Permanent Token Loss — (File: evm/src/omni-bridge/contracts/HlBridgeToken.sol)

---

### Summary

`HlBridgeToken.coreReceiveWithData` decodes the user-supplied `data` field with bare `abi.decode` calls and no error handling. If a HyperCore user submits malformed payload bytes, the decode reverts, rolling back the entire EVM callback. Because HyperLiquid's system deducts the HyperCore-side balance before firing the EVM callback, the user's tokens are permanently lost: gone from HyperCore, and the `_systemAddress` pool on HyperEVM is left unchanged with no recovery path.

---

### Finding Description

`coreReceiveWithData` is the HyperCore → HyperEVM callback entry point. It is called exclusively by `_systemAddress` (the HyperLiquid system address), but the `data` payload it carries is fully controlled by the originating HyperCore user who triggered `sendToEvmWithData`. [1](#0-0) 

After reading the first byte as `action`, the remainder `tail = data[1:]` is passed directly to `abi.decode`:

**Branch `ACTION_TRANSFER` (0x00):**
```solidity
address recipient = abi.decode(tail, (address));
``` [2](#0-1) 

**Branch `ACTION_INIT_TRANSFER` (0x01):**
```solidity
(uint128 fee, string memory recipient, string memory message) = abi
    .decode(tail, (uint128, string, string));
``` [3](#0-2) 

Neither call is wrapped in any try/catch or pre-validation. `abi.decode` in Solidity reverts unconditionally on malformed input (wrong length, invalid offset pointers, etc.). There is no fallback, no recovery, and no event emitted on failure.

The contract's own NatSpec acknowledges the pool accounting model:

> *"HyperLiquid does NOT pre-transfer tokens before this call fires … We pull from `_systemAddress` ourselves."* [4](#0-3) 

This means the `_systemAddress` pool is the standing mirror of total HyperCore-side balance. When the EVM callback reverts, the pool is untouched — but the HyperCore-side deduction has already been committed by the HyperLiquid system before the callback fires. The user's tokens are therefore irrecoverable.

---

### Impact Explanation

**Critical — Permanent, irrecoverable loss of user funds.**

A HyperCore user who sends `sendToEvmWithData` with a malformed `data` field (e.g., `tail` that is not a valid ABI-encoded `address` for action `0x00`, or not a valid `(uint128, string, string)` tuple for action `0x01`) causes:

1. `coreReceiveWithData` to revert entirely.
2. No ERC-20 transfer on HyperEVM (pool unchanged).
3. HyperCore-side balance already deducted — no rollback path exists in the Omni Bridge code.
4. Tokens are permanently stuck in the `_systemAddress` pool with no per-user accounting to attribute or return them.

This matches the allowed impact: **"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."**

---

### Likelihood Explanation

**Medium.**

- The `data` field is entirely user-controlled. Any HyperCore user can craft a `sendToEvmWithData` call with a valid action byte (`0x00` or `0x01`) followed by bytes that are not valid ABI-encoded arguments.
- No privileged role, leaked key, or colluding party is required — this is a pure unprivileged user action.
- Accidental triggering is also plausible: a user who misformats the recipient address or fee encoding will silently lose funds with no on-chain error message (the revert is swallowed by the HyperLiquid system layer).

---

### Recommendation

1. **Validate `tail` length before decoding.** For `ACTION_TRANSFER`, require `tail.length == 32`. For `ACTION_INIT_TRANSFER`, require a minimum length consistent with the ABI encoding of `(uint128, string, string)`.

2. **Use a try/catch-compatible decoding pattern.** Wrap the decode in an internal call to an external helper contract so Solidity's `try/catch` can intercept the revert:
   ```solidity
   try this._decodeTransfer(tail) returns (address recipient) {
       _update(_systemAddress, recipient, amount);
   } catch {
       // emit event, refund to pool, or revert with clear error
       revert InvalidActionData();
   }
   ```

3. **Emit a recoverable-failure event** so that off-chain monitoring can detect and flag stuck pool balances before they accumulate.

---

### Proof of Concept

1. HyperCore user calls `sendToEvmWithData` targeting the `HyperliquedBridgeToken` contract with:
   - `data = bytes([0x00]) + bytes("not-32-bytes")` (action = `ACTION_TRANSFER`, tail is not a valid ABI-encoded address)
2. HyperLiquid system deducts the user's HyperCore balance and calls `coreReceiveWithData(from, ..., amount, data)`.
3. Execution reaches `abi.decode(tail, (address))` at line 121.
4. `abi.decode` reverts because `tail` is not 32 bytes.
5. The entire `coreReceiveWithData` call reverts; no ERC-20 transfer occurs.
6. The `_systemAddress` pool is unchanged; the user's HyperCore tokens are gone.
7. No recovery mechanism exists in the contract. [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L88-96)
```text
    /// and not used here; all routing info comes from `data`.
    /// @dev Accounting model: the 3-arg `mint` parks HyperCore-bound tokens at
    /// `_systemAddress`, so that account holds the standing pool that mirrors total
    /// HyperCore-side balance. HyperLiquid does NOT pre-transfer tokens before this
    /// call fires (the HL system address holds no real ERC20 balance — Circle's
    /// CoreDepositWallet pattern shows the same, with its own pool at `address(this)`).
    /// We pull from `_systemAddress` ourselves; an insufficient pool is a safe revert
    /// that signals an accounting drift between HyperCore and HyperEVM.
    ///
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L106-141)
```text
    function coreReceiveWithData(
        address from,
        bytes32 /*destinationRecipient*/,
        uint32 /*destinationChainId*/,
        uint256 amount,
        uint64 /*coreNonce*/,
        bytes calldata data
    ) external override {
        if (msg.sender != _systemAddress) revert NotSystemAddress();
        if (data.length == 0) revert EmptyActionData();

        uint8 action = uint8(data[0]);
        bytes calldata tail = data[1:];

        if (action == ACTION_TRANSFER) {
            address recipient = abi.decode(tail, (address));
            _update(_systemAddress, recipient, amount);
        } else if (action == ACTION_INIT_TRANSFER) {
            (uint128 fee, string memory recipient, string memory message) = abi
                .decode(tail, (uint128, string, string));
            uint128 amount128 = amount.toUint128();
            _update(_systemAddress, address(this), amount);
            IOmniBridgeInitTransfer(owner()).initTransfer(
                address(this),
                amount128,
                fee,
                0,
                recipient,
                message
            );
        } else {
            revert UnknownAction(action);
        }

        emit CoreReceived(from, action, amount, data);
    }
```
