### Title
`initTransfer1155()` Accepts ERC1155 Tokens Without Validating Prior `logMetadata1155()` Registration, Enabling Permanent Token Freeze — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer1155()` locks ERC1155 tokens into the bridge without verifying that `logMetadata1155()` was previously called for the same `(tokenAddress, tokenId)` pair. If a user calls `initTransfer1155()` before `logMetadata1155()` has been called and the corresponding token deployed on NEAR, the NEAR-side `fin_transfer_callback` panics with `TokenDecimalsNotFound` because the virtual `deterministicToken` address is absent from `token_decimals`. Depending on whether the prover marks the proof as consumed before the callback panics (a well-known NEAR async-model hazard), the ERC1155 tokens become permanently irrecoverable from the bridge vault.

---

### Finding Description

`OmniBridge.sol` uses a two-step registration flow for ERC1155 tokens:

1. **`logMetadata1155(tokenAddress, tokenId)`** — computes a virtual `deterministicToken = keccak256(tokenAddress ‖ tokenId)[0:20]`, populates `multiTokens[deterministicToken]`, and emits a `LogMetadata` event that NEAR uses to deploy and register the bridged token (populating `token_decimals` and `token_address_to_id`).

2. **`initTransfer1155(tokenAddress, tokenId, amount, …)`** — computes the same `deterministicToken`, transfers the ERC1155 from the caller into the bridge, and emits `InitTransfer` with `deterministicToken` as the token address.

The critical gap: `initTransfer1155` contains **no check** that `multiTokens[deterministicToken].tokenAddress != address(0)`, i.e., no enforcement that step 1 was ever executed.

```solidity
// OmniBridge.sol – initTransfer1155 (lines 439-490)
address deterministicToken = deriveDeterministicAddress(tokenAddress, tokenId);

IERC1155(tokenAddress).safeTransferFrom(   // ← tokens locked here unconditionally
    msg.sender, address(this), tokenId, amount, ""
);
// ← NO require(multiTokens[deterministicToken].tokenAddress != address(0))
``` [1](#0-0) 

On the NEAR side, `fin_transfer_callback` unconditionally looks up the token's decimals:

```rust
// near/omni-bridge/src/lib.rs – fin_transfer_callback
let decimals = self
    .token_decimals
    .get(&init_transfer.token)
    .near_expect(BridgeError::TokenDecimalsNotFound);  // ← panics if token unregistered
``` [2](#0-1) 

`token_decimals` is only populated when `deploy_token_callback` succeeds after a valid `LogMetadata` proof is submitted to NEAR:

```rust
// near/omni-bridge/src/lib.rs – deploy_token_internal (called from deploy_token_callback)
self.deploy_token_internal(chain, &metadata.token_address, BasicMetadata { … }, …)
``` [3](#0-2) 

If `logMetadata1155` was never called, `deterministicToken` is absent from `token_decimals`, and `fin_transfer_callback` panics.

**NEAR async-model amplifier**: In NEAR's promise model, `fin_transfer` first calls `verify_proof` (a cross-contract call). If the prover contract marks the proof as consumed during `verify_proof`, and the subsequent `fin_transfer_callback` panics, the proof is permanently spent while the NEAR-side transfer was never completed. The ERC1155 tokens remain locked in the EVM bridge with no on-chain recovery path.

Additionally, even if NEAR could eventually process the transfer and sign a return `finTransfer` back to EVM, `finTransfer` on EVM dispatches based on `multiTokens[payload.tokenAddress]`:

```solidity
// OmniBridge.sol – finTransfer dispatch
MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];
…
} else {
    IERC20(payload.tokenAddress).safeTransfer(payload.recipient, payload.amount);
    // deterministicToken is a virtual address — not a real contract — this reverts
}
``` [4](#0-3) 

If `multiTokens[deterministicToken]` is empty, `finTransfer` falls through to the ERC20 branch and calls `safeTransfer` on the virtual `deterministicToken` address, which is not a deployed contract and will revert, permanently blocking the release path.

---

### Impact Explanation

**Critical — Permanent freezing of user ERC1155 funds in the bridge vault.**

A user who calls `initTransfer1155` without first completing the `logMetadata1155` → NEAR `deploy_token` sequence locks their ERC1155 tokens in the EVM bridge with no on-chain mechanism to recover them. The `finTransfer` release path on EVM also fails for the same token because `multiTokens[deterministicToken]` is unpopulated. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

The `initTransfer1155` function is permissionless and callable by any user. The required prior step (`logMetadata1155` + NEAR `deploy_token`) is not documented at the contract level and is not enforced by any guard. A user unfamiliar with the two-step ERC1155 onboarding flow — or one who calls `initTransfer1155` for a newly-minted ERC1155 token that has never been bridged before — will trigger this condition. The protocol is explicitly designed to be permissionless, increasing the surface area for this user error.

---

### Recommendation

Add a guard in `initTransfer1155` requiring that the token has already been registered via `logMetadata1155`:

```solidity
function initTransfer1155(
    address tokenAddress,
    uint256 tokenId,
    …
) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
    address deterministicToken = deriveDeterministicAddress(tokenAddress, tokenId);

    // Require prior logMetadata1155 registration
    require(
        multiTokens[deterministicToken].tokenAddress != address(0),
        "ERC1155 token not registered: call logMetadata1155 first"
    );

    IERC1155(tokenAddress).safeTransferFrom(…);
    …
}
```

This mirrors the fix recommended for the Cally analog: validate the token type/registration before accepting the deposit.

---

### Proof of Concept

1. Alice holds ERC1155 token `(tokenAddress=0xABC, tokenId=7)`. She has never called `logMetadata1155` for this pair, so `multiTokens[deterministicToken]` is empty and NEAR has no `token_decimals` entry for `deterministicToken`.

2. Alice calls `initTransfer1155(0xABC, 7, 100, 0, 0, "near:alice.near", "")`.
   - `deterministicToken = keccak256(0xABC ‖ 7)[0:20]`
   - `IERC1155(0xABC).safeTransferFrom(Alice, bridge, 7, 100, "")` succeeds — 100 tokens locked.
   - `InitTransfer` event emitted with `deterministicToken` as token address.

3. A relayer calls `fin_transfer` on NEAR with the proof of the `InitTransfer` event.
   - `verify_proof` succeeds and (depending on prover implementation) marks the proof as consumed.
   - `fin_transfer_callback` executes: `self.token_decimals.get(&deterministicToken).near_expect(TokenDecimalsNotFound)` → **panics**.
   - If the proof was already marked consumed in the prover, it cannot be replayed.

4. Alice's 100 ERC1155 tokens are permanently locked in the EVM bridge. There is no admin rescue function. The `finTransfer` release path on EVM also fails because `multiTokens[deterministicToken].tokenAddress == address(0)`, causing the ERC20 fallback branch to call `safeTransfer` on a non-contract address. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L234-270)
```text
    function logMetadata1155(
        address tokenAddress,
        uint256 tokenId
    ) external payable {
        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
        );

        MultiTokenInfo storage multiToken = multiTokens[deterministicToken];

        if (multiToken.tokenAddress == address(0)) {
            multiToken.tokenAddress = tokenAddress;
            multiToken.tokenId = tokenId;
        } else {
            if (
                multiToken.tokenAddress != tokenAddress ||
                multiToken.tokenId != tokenId
            ) {
                revert ERC1155MappingMismatch();
            }
        }

        logMetadataExtension(
            deterministicToken,
            Strings.toHexString(tokenAddress),
            "",
            0
        );

        emit BridgeTypes.LogMetadata(
            deterministicToken,
            Strings.toHexString(tokenAddress),
            "",
            0
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L315-355)
```text
        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
        } else if (customMinters[payload.tokenAddress] != address(0)) {
            ICustomMinter(customMinters[payload.tokenAddress]).mint(
                payload.tokenAddress,
                payload.recipient,
                payload.amount
            );
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
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
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

**File:** near/omni-bridge/src/lib.rs (L700-746)
```rust
    pub fn fin_transfer_callback(
        &mut self,
        #[serializer(borsh)] storage_deposit_actions: &Vec<StorageDepositAction>,
        #[serializer(borsh)] predecessor_account_id: AccountId,
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
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

        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1165-1175)
```rust
        self.deploy_token_internal(
            chain,
            &metadata.token_address,
            BasicMetadata {
                name: metadata.name,
                symbol: metadata.symbol,
                decimals: metadata.decimals,
            },
            attached_deposit,
        )
    }
```
