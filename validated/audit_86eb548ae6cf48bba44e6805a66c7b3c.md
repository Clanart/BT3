### Title
3-arg `mint` in `HyperliquedBridgeToken` Routes All Minted Tokens to `_systemAddress` Instead of Recipient When `finTransfer` Carries Non-Empty Message — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

---

### Summary

`HyperliquedBridgeToken.mint(address, uint256, bytes)` is designed for the HyperCore-bound path, where tokens are intentionally parked at `_systemAddress` to mirror HyperCore-side balance. However, `OmniBridge.finTransfer` unconditionally dispatches to this 3-arg overload whenever `payload.message.length > 0`, regardless of whether the transfer is actually HyperCore-bound. Any unprivileged attacker who initiates a cross-chain transfer with a non-empty `message` field targeting a `HyperliquedBridgeToken` on HyperEVM will cause the recipient to receive zero tokens, with all minted value silently redirected to `_systemAddress`.

---

### Finding Description

**`OmniBridge.finTransfer` dispatch logic** (lines 337–349):

```solidity
} else if (isBridgeToken[payload.tokenAddress]) {
    if (payload.message.length == 0) {
        IBridgeToken(payload.tokenAddress).mint(payload.recipient, payload.amount);
    } else {
        IBridgeToken(payload.tokenAddress).mint(
            payload.recipient, payload.amount, payload.message
        );
    }
}
``` [1](#0-0) 

The sole branching condition is `payload.message.length == 0`. There is no check on the token type, no check on whether the transfer is HyperCore-bound, and no restriction on who can supply a non-empty `message`.

**`HyperliquedBridgeToken.mint` (3-arg)** (lines 76–83):

```solidity
function mint(address account, uint256 value, bytes memory) external override onlyOwner {
    _mint(account, value);
    _update(account, _systemAddress, value);
}
``` [2](#0-1) 

`_mint(account, value)` credits `value` tokens to `account`. The immediately following `_update(account, _systemAddress, value)` is an ERC-20 internal transfer that moves the entire `value` from `account` to `_systemAddress`. The net effect on `account` is zero — all minted tokens end up at `_systemAddress`.

This is by design for the HyperCore path (tokens parked at `_systemAddress` represent the HyperCore-side pool), but it is catastrophically wrong when triggered from `finTransfer` for a normal cross-chain transfer that happens to carry any non-empty `message` bytes.

**Contrast with the base `BridgeToken.mint` (3-arg)** (lines 54–60):

```solidity
function mint(address account, uint256 value, bytes memory) external virtual onlyOwner {
    _mint(account, value);
}
``` [3](#0-2) 

The base implementation simply mints to `account` and stops. `HyperliquedBridgeToken` overrides this with the additional `_update` call, creating the divergence.

---

### Impact Explanation

- The intended recipient receives **0 tokens** after `finTransfer` completes.
- `_systemAddress` receives the full minted amount.
- The bridge nonce is consumed (`completedTransfers[nonce] = true`), so the transfer cannot be replayed or corrected.
- Tokens are permanently misdirected: `_systemAddress` is the Hyperliquid system address; there is no recovery path for tokens that land there via this incorrect route.
- This is **direct theft / irrecoverable loss of bridged assets** from the intended recipient.

---

### Likelihood Explanation

The `message` field in `initTransfer` is a free-form `string calldata` with no restrictions:

```solidity
function initTransfer(
    address tokenAddress, uint128 amount, uint128 fee, uint128 nativeFee,
    string calldata recipient, string calldata message
) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
``` [4](#0-3) 

Any caller on any source chain (NEAR, EVM, Solana, StarkNet) can supply a non-empty `message`. The NEAR MPC signs the entire `TransferMessagePayload` including the `message` bytes verbatim — it has no knowledge of the destination token type and applies no per-token-type validation. The relayer then submits the signed payload to `finTransfer`. No privileged access is required at any step.

The attack is trivially reproducible: pass `message = abi.encode("x")` (or any non-zero-length bytes) in `initTransfer` targeting a `HyperliquedBridgeToken` address.

---

### Recommendation

**Option A (preferred):** Override `finTransfer` dispatch in the Hyperliquid-specific bridge subclass to always call the 2-arg `mint` for normal cross-chain transfers, reserving the 3-arg path exclusively for HyperCore-originated mints.

**Option B:** In `HyperliquedBridgeToken.mint(address, uint256, bytes)`, inspect the `bytes` argument and only execute the `_update` to `_systemAddress` when the message encodes a HyperCore-specific sentinel, reverting otherwise.

**Option C:** Remove the 3-arg `mint` override from `HyperliquedBridgeToken` entirely and handle the HyperCore parking logic through a separate, dedicated entry point that is not reachable from `finTransfer`.

---

### Proof of Concept

```solidity
// 1. Deploy HyperliquedBridgeToken owned by OmniBridge, registered as isBridgeToken.
// 2. Attacker calls initTransfer on source chain with message = abi.encode("x").
// 3. MPC signs TransferMessagePayload{..., message: abi.encode("x")}.
// 4. Relayer calls OmniBridge.finTransfer(sig, payload).
// 5. finTransfer branches to 3-arg mint because payload.message.length > 0.
// 6. HyperliquedBridgeToken.mint(recipient, amount, message):
//      _mint(recipient, amount);           // recipient balance = amount
//      _update(recipient, _systemAddress, amount); // recipient balance = 0
// 7. Assert:
assertEq(token.balanceOf(recipient), 0);
assertEq(token.balanceOf(_systemAddress), amount);
// Transfer is finalized (nonce consumed), recipient has lost all funds.
```

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L337-349)
```text
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-381)
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
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L76-83)
```text
    function mint(
        address account,
        uint256 value,
        bytes memory
    ) external override onlyOwner {
        _mint(account, value);
        _update(account, _systemAddress, value);
    }
```

**File:** evm/src/omni-bridge/contracts/BridgeToken.sol (L54-60)
```text
    function mint(
        address account,
        uint256 value,
        bytes memory
    ) external virtual onlyOwner {
        _mint(account, value);
    }
```
