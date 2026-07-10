### Title
Reentrancy in `initTransfer` / `initTransfer1155` Causes Origin Nonce Desync, Permanently Locking User Funds — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.sol`'s `initTransfer` and `initTransfer1155` increment `currentOriginNonce` at the top of the function but then read it **from storage again** after external token calls when passing it to `initTransferExtension` and `emit`. If a reentrant call occurs during those external calls, the inner call increments `currentOriginNonce` a second time. When the outer call resumes, it reads the already-incremented value and emits an `InitTransfer` event with the **same origin nonce** as the inner call. NEAR's replay-protection set (`finalised_transfers`) then rejects the second message, permanently locking the outer call's tokens in the bridge with no recovery path.

---

### Finding Description

In `initTransfer` (lines 381–437 of `OmniBridge.sol`):

```solidity
function initTransfer(...) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
    currentOriginNonce += 1;          // ← nonce incremented here (e.g. becomes N)
    ...
    // ── external calls ──────────────────────────────────────────────────────
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount); // ← reentrancy point
    // ────────────────────────────────────────────────────────────────────────
    initTransferExtension(
        msg.sender, tokenAddress,
        currentOriginNonce,           // ← reads storage AFTER external call; may now be N+1
        ...
    );
    emit BridgeTypes.InitTransfer(
        msg.sender, tokenAddress,
        currentOriginNonce,           // ← same stale read
        ...
    );
}
``` [1](#0-0) 

The same pattern exists in `initTransfer1155`: [2](#0-1) 

`currentOriginNonce` is a **storage variable**, not a local. Any reentrant call that reaches `currentOriginNonce += 1` before the outer call reads it will cause both calls to use the same nonce value.

The `OmniBridgeWormhole` extension publishes a Wormhole message carrying `originNonce` inside `initTransferExtension`: [3](#0-2) 

On the NEAR side, `finalised_transfers` is a set keyed on `TransferId` (which encodes `origin_chain + origin_nonce`). The second message with the same nonce is rejected as a replay, and the corresponding tokens remain locked in the EVM bridge forever with no refund mechanism. [4](#0-3) 

---

### Impact Explanation

**Critical — Permanent, irrecoverable lock of user funds.**

When the nonce desync is triggered:

1. Two `InitTransfer` events are emitted on EVM with the same `originNonce` (N+1).
2. NEAR processes the first message (inner call) and marks nonce N+1 as finalised.
3. NEAR rejects the second message (outer call) as a replay.
4. The outer call's tokens — already transferred into the bridge — are permanently locked with no on-chain recovery path.

---

### Likelihood Explanation

**Medium.** The bridge imposes no token whitelist on `initTransfer`; any ERC-20 address is accepted. An attacker can:

- Deploy a malicious ERC-20 whose `transferFrom` calls back into `initTransfer` (or `initTransfer1155`) with a second token.
- List the token on a DEX or distribute it via an airdrop.
- When a victim calls `initTransfer` with the malicious token, the callback fires, the inner call increments `currentOriginNonce`, and the outer call's nonce is silently overwritten.

Tokens with legitimate transfer hooks (fee-on-transfer, ERC-777-style callbacks, or protocol-controlled tokens with `beforeTransfer` hooks) can trigger this accidentally without any attacker involvement.

---

### Recommendation

Save `currentOriginNonce` to a **stack-local variable** before any external call, and use only that local variable in `initTransferExtension` and `emit`:

```solidity
function initTransfer(...) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
    currentOriginNonce += 1;
    uint64 originNonce = currentOriginNonce;   // ← capture before external calls
    ...
    IERC20(tokenAddress).safeTransferFrom(...);
    ...
    initTransferExtension(msg.sender, tokenAddress, originNonce, ...);
    emit BridgeTypes.InitTransfer(msg.sender, tokenAddress, originNonce, ...);
}
```

Apply the same fix to `initTransfer1155`. Alternatively, add a `nonReentrant` modifier (OpenZeppelin `ReentrancyGuard`) to both functions.

---

### Proof of Concept

**Setup:**

- `OmniBridgeWormhole` is deployed; `currentOriginNonce = 4`.
- Attacker deploys `EvilToken` whose `transferFrom(from, to, amount)` re-enters `initTransfer` with a second token (e.g., USDC) before completing the transfer.
- Attacker holds USDC and has approved the bridge.

**Execution:**

1. Attacker calls `initTransfer(EvilToken, 1, 0, 0, "near:victim", "")`.
2. `currentOriginNonce` becomes **5**.
3. `safeTransferFrom(attacker, bridge, 1)` is called on `EvilToken`.
4. `EvilToken.transferFrom` calls back: `initTransfer(USDC, 1000, 0, 0, "near:attacker", "")`.
   - Inner call: `currentOriginNonce` becomes **6**.
   - USDC transferred to bridge; Wormhole message published with `originNonce = 6`; event emitted with nonce **6**.
   - Inner call returns.
5. `EvilToken.transferFrom` completes (transfers 1 wei of `EvilToken` to bridge).
6. Outer call resumes; reads `currentOriginNonce = 6` (not 5).
7. `initTransferExtension` publishes a second Wormhole message with `originNonce = 6`.
8. `emit InitTransfer(..., originNonce = 6, ...)` — **duplicate nonce**.

**Result on NEAR:**

- NEAR processes the first nonce-6 message → USDC minted to attacker.
- NEAR rejects the second nonce-6 message → `EvilToken` (or any real token in the outer call) permanently locked in the EVM bridge.
- Nonce **5** is never emitted; it is silently skipped.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L381-437)
```text
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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L118-150)
```text
    function initTransferExtension(
        address sender,
        address tokenAddress,
        uint64 originNonce,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message,
        uint256 value
    ) internal override {
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

        wormholeNonce++;
    }
```

**File:** near/omni-bridge/src/lib.rs (L220-243)
```rust
pub struct Contract {
    pub factories: LookupMap<ChainKind, OmniAddress>,
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
    pub finalised_transfers: LookupSet<TransferId>,
    pub finalised_utxo_transfers: LookupSet<UnifiedTransferId>,
    pub fast_transfers: LookupMap<FastTransferId, FastTransferStatusStorage>,
    pub token_id_to_address: LookupMap<(ChainKind, AccountId), OmniAddress>,
    pub token_address_to_id: LookupMap<OmniAddress, AccountId>,
    pub token_decimals: LookupMap<OmniAddress, Decimals>,
    pub deployed_tokens: LookupSet<AccountId>,
    pub deployed_tokens_v2: LookupMap<AccountId, ChainKind>,
    pub token_deployer_accounts: LookupMap<ChainKind, AccountId>,
    pub mpc_signer: AccountId,
    pub current_origin_nonce: Nonce,
    // We maintain a separate nonce for each chain to optimize the storage usage on Solana by reducing the gaps.
    pub destination_nonces: LookupMap<ChainKind, Nonce>,
    pub accounts_balances: LookupMap<AccountId, StorageBalance>,
    pub wnear_account_id: AccountId,
    pub provers: UnorderedMap<ChainKind, AccountId>,
    pub init_transfer_promises: LookupMap<AccountId, CryptoHash>,
    pub utxo_chain_connectors: HashMap<ChainKind, UTXOChainConfig>,
    pub migrated_tokens: LookupMap<AccountId, AccountId>,
    pub locked_tokens: LookupMap<(ChainKind, AccountId), u128>,
}
```
