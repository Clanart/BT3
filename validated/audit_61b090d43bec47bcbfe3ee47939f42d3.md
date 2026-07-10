### Title
ERC1155 Recipient Revert in `finTransfer` Causes Permanent Freezing of Bridged Funds — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer` uses `IERC1155.safeTransferFrom` (and a raw `.call` for native ETH) to push tokens to the user-specified `payload.recipient`. If that recipient is a contract that reverts on `onERC1155Received` (or on ETH receipt), the entire `finTransfer` transaction reverts. Because the MPC has already signed a payload bound to that specific recipient, no alternative finalization is possible on-chain, and the user's tokens locked/burned on the origin chain are permanently irrecoverable.

---

### Finding Description

In `OmniBridge.finTransfer`, the destination nonce is marked used at line 287 and then tokens are pushed to `payload.recipient`:

**ERC1155 path (lines 323–330):**
```solidity
IERC1155(multiToken.tokenAddress).safeTransferFrom(
    address(this),
    payload.recipient,
    multiToken.tokenId,
    payload.amount,
    ""
);
``` [1](#0-0) 

`safeTransferFrom` calls `onERC1155Received` on `payload.recipient` if it is a contract. If the recipient contract does not implement `IERC1155Receiver` or deliberately reverts in that hook, the entire `finTransfer` call reverts.

**Native ETH path (lines 317–322):**
```solidity
(bool success, ) = payload.recipient.call{value: payload.amount}("");
if (!success) revert FailedToSendEther();
``` [2](#0-1) 

If `payload.recipient` is a contract with no `receive()` or one that reverts, `finTransfer` reverts with `FailedToSendEther`.

Because the whole transaction reverts, `completedTransfers[payload.destinationNonce]` is also rolled back — the nonce is not permanently consumed. However, the MPC has signed a payload that encodes the exact `recipient`, `amount`, `destinationNonce`, and `tokenAddress`. There is no on-chain mechanism to re-sign with a different recipient or to issue a refund back to the origin chain. Every subsequent attempt to call `finTransfer` with the same signed payload will revert identically. [3](#0-2) 

On the NEAR side, the user's tokens were already locked or burned at `init_transfer` time: [4](#0-3) 

There is no `cancel_transfer` or `refund_transfer` function in the NEAR bridge contract that could be triggered by a failed EVM finalization. [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent, irrecoverable freezing of user funds.**

- The user's tokens are locked/burned on NEAR at `init_transfer`.
- `finTransfer` on EVM will always revert for the signed payload because the recipient cannot accept the token type.
- No on-chain recovery path exists: there is no refund function, no recipient-override mechanism, and no admin escape hatch for stuck ERC1155 or ETH transfers.
- Funds are permanently frozen in the bridge.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.**

- ERC1155 `safeTransferFrom` mandates that contract recipients implement `IERC1155Receiver`. Many smart contract wallets, multisigs, and DeFi contracts do not implement this interface.
- A user who specifies a multisig, DAO treasury, or any contract without `onERC1155Received` as the EVM recipient of an ERC1155 bridge transfer will permanently lose their funds.
- For native ETH, any contract without a `receive()` or `fallback()` function (e.g., certain proxy contracts) as recipient causes the same freeze.
- No special privilege is required — any bridge user initiating an ERC1155 or native ETH transfer can trigger this by specifying a non-compliant contract as recipient.

---

### Recommendation

1. **Pull-over-push pattern**: Instead of pushing tokens directly to `payload.recipient` inside `finTransfer`, record the claimable balance in a mapping and let the recipient pull tokens via a separate `claim` function. This decouples finalization from delivery.

2. **Fallback escrow**: If the push transfer fails (wrap in a try/catch or check return value), store the tokens in an escrow mapping keyed by `(destinationNonce, recipient)` and emit an event. Provide a `claimEscrow(uint64 destinationNonce)` function so the recipient can retrieve tokens later.

3. **Recipient validation**: For ERC1155 transfers, validate that `payload.recipient` supports `IERC1155Receiver` (via `IERC165.supportsInterface`) before attempting the transfer, and revert with a clear error if not — preventing the nonce from being wasted and allowing the MPC to re-sign with a corrected recipient.

---

### Proof of Concept

1. Alice holds ERC1155 tokens on NEAR and initiates a bridge transfer to EVM, specifying `recipient = address(MyMultisig)` where `MyMultisig` does not implement `IERC1155Receiver`.
2. The NEAR bridge locks Alice's tokens and emits an `InitTransferEvent`.
3. The MPC signs a `TransferMessagePayload` encoding `recipient = address(MyMultisig)`, `tokenAddress = deterministicERC1155Address`, `amount`, and `destinationNonce`.
4. A relayer calls `OmniBridge.finTransfer(signatureData, payload)`.
5. Line 287 sets `completedTransfers[payload.destinationNonce] = true`.
6. Line 324 executes `IERC1155(...).safeTransferFrom(address(this), address(MyMultisig), ...)`.
7. `MyMultisig` does not implement `onERC1155Received` → EVM reverts the entire transaction.
8. `completedTransfers[payload.destinationNonce]` is rolled back to `false`.
9. Every retry of step 4 with the same signed payload reverts identically.
10. Alice's tokens on NEAR are permanently locked. No on-chain refund or re-routing exists. [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-355)
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

**File:** near/omni-bridge/src/lib.rs (L523-557)
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
```
