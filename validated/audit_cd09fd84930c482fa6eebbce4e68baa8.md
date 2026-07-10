Let me look at the NEAR `fin_transfer` function and the EVM `finTransfer` recovery path to complete the analysis.

### Title
Unregistered ERC20 Tokens Permanently Locked in Bridge via Unchecked `initTransfer` - (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`initTransfer` on the EVM bridge accepts any ERC20 `tokenAddress` without verifying it is registered in the bridge's token mappings. For tokens not in `isBridgeToken`, `customMinters`, or `multiTokens`, the function locks the tokens in the contract via `safeTransferFrom`. The NEAR `fin_transfer_callback` then panics with `TokenDecimalsNotFound` because the EVM token address has no entry in `token_decimals`. No refund or recovery path exists on the EVM side, making the locked tokens permanently irrecoverable.

---

### Finding Description

**EVM side — no registration guard:**

`initTransfer` dispatches on three registered token types and falls through to a bare `safeTransferFrom` for everything else: [1](#0-0) 

```solidity
if (customMinters[tokenAddress] != address(0)) {
    ...burn path...
} else if (isBridgeToken[tokenAddress]) {
    BridgeToken(tokenAddress).burn(msg.sender, amount);
} else {
    // ← any arbitrary ERC20 reaches here
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
}
```

There is no `require` that the token is known to the bridge before locking it. The `InitTransfer` event is then emitted unconditionally. [2](#0-1) 

**NEAR side — hard panic on unregistered token:**

The relayer submits the EVM proof to NEAR `fin_transfer`, which calls `fin_transfer_callback`. The callback immediately looks up `token_decimals` keyed by the EVM token address (`OmniAddress::Eth(...)`): [3](#0-2) 

```rust
let decimals = self
    .token_decimals
    .get(&init_transfer.token)
    .near_expect(BridgeError::TokenDecimalsNotFound);
```

`token_decimals` is only populated by `add_token`, which is called exclusively from `deploy_token` and `bind_token` — both of which require a valid proof/signature from a trusted source. An unregistered EVM token has no entry, so `near_expect` panics and the NEAR transaction reverts. [4](#0-3) 

**No recovery path:**

The EVM `finTransfer` (the only function that releases locked ERC20s) requires a valid NEAR MPC signature: [5](#0-4) 

The NEAR bridge only produces MPC signatures via `sign_transfer`, which operates on `pending_transfers` entries created by NEAR-originated transfers — not EVM-originated ones. There is no admin rescue, no cancel, and no refund function in `OmniBridge.sol` for locked ERC20s. [6](#0-5) 

The EVM CLAUDE.md explicitly states this as a security invariant: *"Never mint, transfer, or unlock tokens to a recipient without first verifying a valid MPC signature."* — meaning no out-of-band admin rescue is possible by design. [7](#0-6) 

---

### Impact Explanation

Any ERC20 tokens sent via `initTransfer` with an unregistered `tokenAddress` are permanently locked in the EVM bridge contract with no recovery mechanism. The NEAR side will always reject the finalization, and the EVM side has no path to release the tokens without a NEAR MPC signature that will never be produced for this transfer.

**Impact:** Critical — permanent, irrecoverable lock of user ERC20 funds.

---

### Likelihood Explanation

The attack requires no privilege. Any user who calls `initTransfer` with a token that has not been registered via `deployToken`/`addCustomToken` on EVM and `deploy_token`/`bind_token` on NEAR will trigger this. This includes:
- Users who call `logMetadata` (which does NOT register the token) and then immediately attempt `initTransfer`
- Users who bridge a token that was registered on EVM but whose NEAR-side registration (`bind_token`/`deploy_token`) has not yet been completed
- Any user who simply passes an arbitrary ERC20 address

The `logMetadata` function in particular creates a false sense of readiness — it emits a `LogMetadata` event but does not register the token in any mapping, yet `initTransfer` will accept the token address immediately after. [8](#0-7) 

---

### Recommendation

Add a registration guard at the top of the `else` branch in `initTransfer`, or at the function entry for non-native tokens:

```solidity
} else {
    require(
        bytes(ethToNearToken[tokenAddress]).length > 0,
        "ERR_TOKEN_NOT_REGISTERED"
    );
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
}
```

This ensures only tokens with a known NEAR-side mapping can be locked, matching the invariant that every locked ERC20 must be claimable on the destination chain.

---

### Proof of Concept

1. Deploy any ERC20 token `T` not registered in the bridge.
2. Call `OmniBridge.initTransfer(address(T), amount, 0, 0, "recipient.near", "")` with sufficient ETH for `nativeFee=0`.
3. Observe `safeTransferFrom` succeeds — `amount` tokens are now held by the bridge.
4. Observe `InitTransfer` event emitted.
5. Relayer submits proof to NEAR `fin_transfer`. NEAR `fin_transfer_callback` panics at `token_decimals.get(&init_transfer.token).near_expect(BridgeError::TokenDecimalsNotFound)`.
6. No `finTransfer` call on EVM can ever release the tokens (no valid MPC signature will be produced).
7. Tokens are permanently locked. [9](#0-8) [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L224-232)
```text
    function logMetadata(address tokenAddress) external payable {
        string memory name = IERC20Metadata(tokenAddress).name();
        string memory symbol = IERC20Metadata(tokenAddress).symbol();
        uint8 decimals = IERC20Metadata(tokenAddress).decimals();

        logMetadataExtension(tokenAddress, name, symbol, decimals);

        emit BridgeTypes.LogMetadata(tokenAddress, name, symbol, decimals);
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-367)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

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

        finTransferExtension(payload);

        emit BridgeTypes.FinTransfer(
            payload.originChain,
            payload.originNonce,
            payload.tokenAddress,
            payload.amount,
            payload.recipient,
            payload.feeRecipient
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L394-412)
```text
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

**File:** near/omni-bridge/src/lib.rs (L700-718)
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
```

**File:** evm/CLAUDE.md (L35-36)
```markdown
- **No token release without signature**: Never mint, transfer, or unlock tokens to a recipient without first verifying a valid MPC signature. No admin function, emergency path, or refactor may bypass this — it is the only authorization gate for finTransfer
- **Event–transfer atomicity**: `InitTransfer` must only be emitted in a code path where tokens have already been burned/locked in the same transaction. If the token transfer reverts or is skipped, the event must not emit — the NEAR side will treat any emitted event as proof that tokens are held
```
