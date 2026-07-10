### Title
Removing a Custom Token via `removeCustomToken()` Permanently Freezes Pending NEAR→EVM Transfers — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.sol` exposes a `removeCustomToken()` admin function that deletes a token's entries from `isBridgeToken`, `customMinters`, `ethToNearToken`, and `nearToEthToken`. The `finTransfer()` function — which finalizes NEAR→EVM transfers by minting tokens to recipients — gates its mint path on the **current** state of `isBridgeToken` and `customMinters`. If a custom token is removed while any NEAR→EVM transfer for that token is in-flight (tokens already locked/burned on NEAR, MPC signature already issued), every subsequent `finTransfer()` call for that token falls through all mint branches and reaches the fallback ERC-20 `safeTransfer` path. Because the bridge holds no balance of a minted custom token, the call reverts. The user's funds are irrecoverably frozen: burned on NEAR, unmintable on EVM, with no cancellation or refund path.

---

### Finding Description

**Root cause — `removeCustomToken()` clears the live registry with no pending-transfer guard:** [1](#0-0) 

```solidity
function removeCustomToken(address tokenAddress) external onlyRole(DEFAULT_ADMIN_ROLE) {
    delete isBridgeToken[tokenAddress];
    delete nearToEthToken[ethToNearToken[tokenAddress]];
    delete ethToNearToken[tokenAddress];
    delete customMinters[tokenAddress];
}
```

No check is made for in-flight transfers. After this call, both `isBridgeToken[tokenAddress]` and `customMinters[tokenAddress]` are `false`/`address(0)`.

**Affected path — `finTransfer()` dispatches on the current registry:** [2](#0-1) 

The dispatch chain in `finTransfer()` is:

1. `payload.tokenAddress == address(0)` → send ETH  
2. `multiTokens[payload.tokenAddress].tokenAddress != address(0)` → ERC-1155 transfer  
3. `customMinters[payload.tokenAddress] != address(0)` → `ICustomMinter.mint`  
4. `isBridgeToken[payload.tokenAddress]` → `IBridgeToken.mint`  
5. **else** → `IERC20.safeTransfer` (locked-token path)

After `removeCustomToken()`, conditions 3 and 4 are both false. The call falls to the `safeTransfer` path. Because the bridge holds zero balance of a minted custom token (it was never locked, only minted on demand), the `safeTransfer` reverts. The nonce is not durably consumed (the revert rolls back `completedTransfers[nonce] = true`), so the user can retry — but every retry reverts for the same reason. There is no cancellation or refund mechanism on the NEAR side.

**NEAR-side lock is permanent:**

On NEAR, `init_transfer` locks or burns the user's tokens and stores the transfer in `pending_transfers`. [3](#0-2) 

There is no `cancel_transfer` or refund path. Once the transfer is recorded and the tokens are gone from the user's NEAR balance, the only recovery path is a successful `finTransfer()` on EVM — which is now permanently blocked.

---

### Impact Explanation

**Critical — Permanent, irrecoverable freezing of user funds.**

Any user who initiated a NEAR→EVM transfer for a custom token before `removeCustomToken()` was called loses their tokens permanently:
- Tokens are burned/locked on NEAR with no refund path.
- `finTransfer()` on EVM reverts for every retry.
- The MPC signature is valid but useless.
- No admin escape hatch exists to rescue the stuck funds.

This matches the allowed impact: *"Critical. Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** `removeCustomToken()` is a routine admin operation (e.g., deprecating a compromised or migrated token). The admin has no on-chain visibility into in-flight NEAR→EVM transfers at the time of removal. A transfer can be in-flight for minutes to hours (waiting for MPC signing, relayer submission, or user action). Any overlap between a removal and an in-flight transfer causes permanent loss. The admin need not be malicious — the protocol simply provides no guard.

---

### Recommendation

Mirror the fix applied in the LevelMinting report: maintain a separate historical registry of tokens that have ever been registered, and use that registry — not the live `isBridgeToken`/`customMinters` mapping — to gate `finTransfer()` minting.

```solidity
// New historical set — tokens that were ever registered as custom tokens
mapping(address => bool) public wasEverCustomToken;

function addCustomToken(...) external onlyRole(DEFAULT_ADMIN_ROLE) {
    isBridgeToken[tokenAddress] = true;
    wasEverCustomToken[tokenAddress] = true;   // <-- add
    customMinters[tokenAddress] = customMinter;
    ...
}

// In finTransfer(), replace:
//   } else if (customMinters[payload.tokenAddress] != address(0)) {
// with a check that also consults wasEverCustomToken and a stored minter snapshot,
// OR require that removeCustomToken() can only be called when no pending transfers exist.
```

Alternatively, add a `pendingTransferCount` per token and require it to be zero before `removeCustomToken()` succeeds.

---

### Proof of Concept

1. Admin calls `addCustomToken("near-token.near", tokenAddr, minterAddr, 18)`.  
   → `isBridgeToken[tokenAddr] = true`, `customMinters[tokenAddr] = minterAddr`.

2. User on NEAR calls `ft_transfer_call` → `init_transfer` → tokens burned on NEAR, transfer stored in `pending_transfers`.

3. Relayer calls `sign_transfer` on NEAR → MPC signature issued.

4. Admin calls `removeCustomToken(tokenAddr)`.  
   → `isBridgeToken[tokenAddr] = false`, `customMinters[tokenAddr] = address(0)`.

5. User/relayer calls `finTransfer(signatureData, payload)` on EVM.  
   - Signature verifies against `nearBridgeDerivedAddress` ✓  
   - `payload.tokenAddress != address(0)` → not ETH  
   - `multiTokens[tokenAddr].tokenAddress == address(0)` → not ERC-1155  
   - `customMinters[tokenAddr] == address(0)` → not custom minter  
   - `isBridgeToken[tokenAddr] == false` → not bridge token  
   - Falls to `IERC20(tokenAddr).safeTransfer(recipient, amount)` → **reverts** (bridge balance = 0)

6. User retries indefinitely — always reverts. Funds on NEAR are permanently lost.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L120-127)
```text
    function removeCustomToken(
        address tokenAddress
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        delete isBridgeToken[tokenAddress];
        delete nearToEthToken[ethToNearToken[tokenAddress]];
        delete ethToNearToken[tokenAddress];
        delete customMinters[tokenAddress];
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-350)
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
```

**File:** near/omni-bridge/src/lib.rs (L523-560)
```rust
    fn init_transfer(
        &mut self,
        sender_id: AccountId,
        signer_id: AccountId,
        token_id: AccountId,
        amount: U128,
        init_transfer_msg: InitTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );

        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );

        let required_storage_balance =
            self.required_balance_for_init_transfer_message(transfer_message.clone());
```
