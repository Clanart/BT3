### Title
`OmniBridge.initTransfer` Does Not Validate Token Registration Before Locking User Funds - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

The `initTransfer` function in `OmniBridge.sol` accepts any ERC20 `tokenAddress` without verifying that the token is registered in the bridge's token registry (`ethToNearToken` mapping). If a user calls `initTransfer` with an unregistered token, their tokens are transferred into the bridge contract and locked, but the NEAR side cannot finalize the transfer because the token has no registered mapping, resulting in permanent or indefinite locking of user funds with no on-chain refund path.

---

### Finding Description

`initTransfer` (lines 373–437) routes token handling through three branches:

```solidity
if (customMinters[tokenAddress] != address(0)) {
    // burn via custom minter
} else if (isBridgeToken[tokenAddress]) {
    // burn bridge token
} else {
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
}
``` [1](#0-0) 

For the third branch (native/unregistered tokens), there is **no check** that `ethToNearToken[tokenAddress]` is non-empty — i.e., that the token is actually registered in the bridge. The function proceeds to lock the tokens in the bridge contract and emits an `InitTransfer` event regardless. [2](#0-1) 

The NEAR bridge contract stores the canonical token registry in `token_address_to_id` and `token_id_to_address`. When `fin_transfer` is called on NEAR with a proof of the EVM `InitTransfer` event, it must resolve the EVM token address to a NEAR token ID. If the token was never registered, this lookup fails and the NEAR transaction panics. Because the panic occurs before `finalised_transfers` is updated, the nonce is not consumed — but the EVM tokens are already locked in the bridge with no refund mechanism. [3](#0-2) 

This is a direct structural analog to the ERC20Gauges bug: the function checks some negative/membership conditions (`customMinters`, `isBridgeToken`) but omits the critical positive registration check (`ethToNearToken[tokenAddress] != ""`), allowing the operation to proceed silently on an unregistered token.

---

### Impact Explanation

A user who calls `initTransfer` with an unregistered ERC20 token address will have their tokens transferred into the bridge contract and locked. The NEAR side cannot finalize the transfer, so no tokens are minted or released on the destination chain. The EVM contract has no refund or withdrawal mechanism for this case. Recovery requires out-of-band admin intervention (registering the token on NEAR and resubmitting the proof), which is not guaranteed and may never occur. This constitutes **permanent or indefinite irrecoverable locking of user funds in the bridge vault flow**, matching the Critical/High allowed impact.

---

### Likelihood Explanation

Any unprivileged user can call `initTransfer` directly with an arbitrary `tokenAddress`. Realistic triggering scenarios include:

- A user bridges a token that was recently removed from the registry (e.g., after `removeCustomToken`) but whose EVM address they still hold.
- A user mistypes or copy-pastes the wrong token address.
- A token is in the process of being onboarded (registered on EVM but not yet on NEAR) and a user initiates a transfer prematurely.

No privileged access, leaked keys, or chain-level attack is required. The entry path is fully public.

---

### Recommendation

Add a registration guard in `initTransfer` before processing any token transfer. For the native-token branch specifically:

```solidity
function initTransfer(
    address tokenAddress,
    uint128 amount,
    uint128 fee,
    uint128 nativeFee,
    string calldata recipient,
    string calldata message
) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
    ...
    if (tokenAddress != address(0)) {
+       require(
+           bytes(ethToNearToken[tokenAddress]).length > 0,
+           "ERR_TOKEN_NOT_REGISTERED"
+       );
        if (customMinters[tokenAddress] != address(0)) {
            ...
        } else if (isBridgeToken[tokenAddress]) {
            ...
        } else {
            IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
        }
    }
    ...
}
```

This mirrors the recommended fix in the ERC20Gauges report: check the positive registration condition (`ethToNearToken[tokenAddress]` is set) in addition to the existing membership checks, so that the function reverts with a clear error instead of silently locking unrecoverable funds.

---

### Proof of Concept

1. `unregisteredToken` is a valid ERC20 deployed on EVM but **not** present in `ethToNearToken` / `nearToEthToken` on the OmniBridge.
2. User approves the bridge and calls:
   ```solidity
   omniBridge.initTransfer(unregisteredToken, 1000e18, 0, 0, "alice.near", "");
   ```
3. Neither `customMinters[unregisteredToken]` nor `isBridgeToken[unregisteredToken]` is set, so the `else` branch executes: `IERC20(unregisteredToken).safeTransferFrom(user, bridge, 1000e18)` — tokens are locked.
4. `InitTransfer` event is emitted with `tokenAddress = unregisteredToken`.
5. A relayer submits the proof to NEAR `fin_transfer`. NEAR calls `get_token_id(&unregisteredToken_omni_address)` → `token_address_to_id.get(...)` returns `None` → NEAR panics.
6. NEAR transaction reverts; `finalised_transfers` is not updated.
7. User's 1000e18 tokens remain locked in the EVM bridge contract indefinitely with no on-chain recovery path. [4](#0-3) [5](#0-4)

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

**File:** near/omni-bridge/src/lib.rs (L36-38)
```rust
use omni_utils::promise::PromiseOrPromiseIndexOrValue;
use std::collections::HashMap;
use std::str::FromStr;
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
