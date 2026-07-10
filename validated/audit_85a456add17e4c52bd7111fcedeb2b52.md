### Title
Unregistered Token Lock Due to Missing Token Registration Validation in EVM `initTransfer` — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
The EVM `initTransfer` function accepts any arbitrary ERC20 token address without checking whether that token is registered in the bridge, while the NEAR `fin_transfer_callback` strictly requires the token to be registered (via `token_decimals` lookup). This inconsistency means a user who initiates a transfer of an unregistered ERC20 token will have their tokens permanently locked in the EVM bridge contract with no on-chain recovery path.

### Finding Description

**Vulnerability class:** Inconsistent validation / missing token-registration whitelist check on the EVM ingress path.

**EVM side — no registration check in `initTransfer`:**

`OmniBridge.sol` `initTransfer` (lines 373–412) accepts any `tokenAddress`. For tokens that are neither a `customMinter` nor a `isBridgeToken`, it silently locks them in the contract:

```solidity
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount
    );
}
```

There is no check that `tokenAddress` is registered in `ethToNearToken`, `nearToEthToken`, `isBridgeToken`, `customMinters`, or `multiTokens`. [1](#0-0) 

**NEAR side — strict registration check in `fin_transfer_callback`:**

When a relayer submits the proof on NEAR, `fin_transfer_callback` (lines 715–718) performs a mandatory lookup of `token_decimals` for the token address carried in the proof. If the token was never registered via `bind_token`, `deploy_token`, or `add_deployed_tokens`, this call panics with `ERR_TOKEN_DECIMALS_NOT_FOUND`, reverting the entire callback:

```rust
let decimals = self
    .token_decimals
    .get(&init_transfer.token)
    .near_expect(BridgeError::TokenDecimalsNotFound);
``` [2](#0-1) 

The factory (emitter) validation that precedes it also passes correctly, so the only failure point is the missing token registration: [3](#0-2) 

**No on-chain rescue path on EVM:**

The EVM `OmniBridge` contract exposes no `rescueTokens`, `emergencyWithdraw`, or equivalent admin function. The only admin functions visible are `addCustomToken`, `removeCustomToken`, `acceptTokenOwnership`, `setMetadata`, and pause controls — none of which can return locked ERC20 tokens to a user. [4](#0-3) 

### Impact Explanation

Any ERC20 token that is not registered in the bridge can be locked in the EVM `OmniBridge` contract by any caller of `initTransfer`. Because the NEAR finalization callback will always panic for unregistered tokens, the corresponding proof can never be successfully submitted, and the locked tokens have no on-chain recovery path. This constitutes an irrecoverable lock of user funds in the bridge vault flow, matching the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

The entry point (`initTransfer`) is fully public and requires no special role. Any bridge user who mistakenly (or experimentally) calls `initTransfer` with a token that has not yet been registered on NEAR — including newly deployed tokens, tokens from chains not yet onboarded, or tokens whose `bind_token`/`deploy_token` transaction has not yet been confirmed — will trigger this condition. The likelihood is **medium**: the permissionless nature of `initTransfer` and the absence of any UI-level guard make accidental misuse realistic.

### Recommendation

**Short term:** Add a token-registration guard at the top of `initTransfer` that rejects any `tokenAddress` not present in at least one of the bridge's registration mappings (`ethToNearToken`, `isBridgeToken`, `customMinters`, `multiTokens`):

```solidity
require(
    tokenAddress == address(0) ||
    bytes(ethToNearToken[tokenAddress]).length > 0 ||
    isBridgeToken[tokenAddress] ||
    customMinters[tokenAddress] != address(0) ||
    multiTokens[tokenAddress].tokenAddress != address(0),
    "ERR_TOKEN_NOT_REGISTERED"
);
```

**Long term:** Adopt a single, consistent validation approach across all bridge entry points (EVM, Starknet, Solana) so that the ingress path and the finalization path enforce the same token-registration invariant. Consider also adding an admin `rescueERC20` function as a safety net for tokens that bypass validation.

### Proof of Concept

1. A user holds 1,000 units of `UnregisteredToken` (a valid ERC20 not in the bridge's mappings).
2. User approves `OmniBridge` and calls:
   ```solidity
   OmniBridge.initTransfer(
       address(UnregisteredToken),
       1000,
       0,        // fee
       0,        // nativeFee
       "user.near",
       ""
   );
   ```
3. `initTransfer` executes `safeTransferFrom(user, address(this), 1000)` — tokens are now held by the bridge. [5](#0-4) 
4. A relayer submits the Wormhole VAA proof to NEAR `fin_transfer`.
5. `fin_transfer_callback` calls `self.token_decimals.get(&init_transfer.token).near_expect(...)` — panics with `ERR_TOKEN_DECIMALS_NOT_FOUND` because the token was never registered. [2](#0-1) 
6. The NEAR callback reverts. The EVM state is already committed. The 1,000 tokens remain locked in `OmniBridge` with no recovery path.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L88-135)
```text
    function addCustomToken(
        string calldata nearTokenId,
        address tokenAddress,
        address customMinter,
        uint8 originDecimals
    ) external payable onlyRole(DEFAULT_ADMIN_ROLE) {
        isBridgeToken[tokenAddress] = true;
        ethToNearToken[tokenAddress] = nearTokenId;
        nearToEthToken[nearTokenId] = tokenAddress;
        customMinters[tokenAddress] = customMinter;

        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();

        deployTokenExtension(
            nearTokenId,
            tokenAddress,
            decimals,
            originDecimals
        );

        emit BridgeTypes.DeployToken(
            tokenAddress,
            nearTokenId,
            name,
            symbol,
            decimals,
            originDecimals
        );
    }

    function removeCustomToken(
        address tokenAddress
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        delete isBridgeToken[tokenAddress];
        delete nearToEthToken[ethToNearToken[tokenAddress]];
        delete ethToNearToken[tokenAddress];
        delete customMinters[tokenAddress];
    }

    function acceptTokenOwnership(
        address tokenAddress
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        BridgeToken(tokenAddress).acceptOwnership();
    }

    function deployToken(
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-413)
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
```

**File:** near/omni-bridge/src/lib.rs (L708-713)
```rust
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L715-718)
```rust
        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);
```
