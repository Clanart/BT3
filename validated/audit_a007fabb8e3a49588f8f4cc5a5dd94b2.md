### Title
Reentrancy via ERC777/ERC1155 Hooks Causes `currentOriginNonce` Collision, Enabling Double-Mint on NEAR - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` and `OmniBridge.initTransfer1155` increment `currentOriginNonce` at the start of the function but read it back from storage **after** an external token call. Because no reentrancy guard is present, a reentrant call triggered by an ERC777 send-hook or an ERC1155 `safeTransferFrom` callback increments the nonce a second time. Both the outer call and the reentrant call then emit `InitTransfer` with the same nonce value (N+1), while nonce N is never emitted. The NEAR bridge's `fin_transfer_callback` does not deduplicate by EVM origin nonce; it assigns its own destination nonce to every submitted proof. A relayer that submits both events will cause NEAR to mint bridged tokens twice for a single lock, breaking bridge collateralization.

---

### Finding Description

In `OmniBridge.sol`, both `initTransfer` and `initTransfer1155` follow this pattern:

```
currentOriginNonce += 1;          // (1) nonce written to storage as N
...
IERC20/IERC1155.safeTransferFrom(...)  // (2) external call — hook fires here
...
initTransferExtension(..., currentOriginNonce, ...);  // (3) reads storage → may be N+1
emit InitTransfer(..., currentOriginNonce, ...);       // (4) reads storage → may be N+1
``` [1](#0-0) [2](#0-1) [3](#0-2) 

The same pattern exists in `initTransfer1155`: [4](#0-3) 

`OmniBridge` does not inherit `ReentrancyGuard` and carries no `nonReentrant` modifier on either function. The only reentrancy-related comments in the file are `slither-disable-next-line` annotations on unrelated paths. [5](#0-4) 

**Reentrant execution trace (ERC777 path):**

1. Attacker calls `initTransfer(erc777Token, amountA, ...)` → `currentOriginNonce` becomes N.
2. `safeTransferFrom` triggers the ERC777 `tokensToSend` hook on the attacker's contract.
3. Hook reenters `initTransfer(legitimateToken, amountY, ...)` → `currentOriginNonce` becomes N+1.
4. Inner call: `initTransferExtension(..., currentOriginNonce=N+1, ...)` and `emit InitTransfer(..., N+1, ...)`.
5. Outer call resumes; reads `currentOriginNonce` from storage → still N+1.
6. Outer call: `initTransferExtension(..., currentOriginNonce=N+1, ...)` and `emit InitTransfer(..., N+1, ...)`.

Result: two `InitTransfer` events carry nonce N+1; nonce N is never emitted.

On the NEAR side, `fin_transfer_callback` generates its own `destination_nonce` for every submitted proof and tracks finality by that destination nonce, not by the EVM `origin_nonce`. There is no guard that rejects a second proof carrying the same EVM origin nonce. [6](#0-5) [7](#0-6) 

A relayer that submits both proofs will cause NEAR to process two separate `fin_transfer` flows, each receiving a unique destination nonce and each minting/unlocking the full `amountY` of the legitimate token.

---

### Impact Explanation

**Critical.** An attacker can cause NEAR to mint bridged tokens twice for a single EVM-side lock. The bridge's collateral (locked EVM tokens) backs only one unit of the bridged supply, but two units are minted on NEAR. This directly breaks bridge collateralization and constitutes unauthorized minting of bridged assets. The attacker profits by `amountY` of the legitimate token at zero cost (the malicious ERC777 token need not transfer real value).

---

### Likelihood Explanation

**Low-to-Medium.** The attack requires:
- A token with a transfer hook (ERC777 is deployed on mainnet; ERC1155 with a malicious `safeTransferFrom` is trivially deployable).
- A relayer that submits both duplicate-nonce events to NEAR without deduplication.

Automated relayers that index all `InitTransfer` events and submit them without filtering will naturally submit both. The attacker fully controls the ERC777 token and the reentrant call parameters, making the exploit deterministic once the hook fires.

---

### Recommendation

1. **Add `nonReentrant` to `initTransfer` and `initTransfer1155`** (import OpenZeppelin `ReentrancyGuardUpgradeable`). This is the minimal fix.

2. **Capture the nonce before the external call** and pass the captured value to `initTransferExtension` and the event, so that even without a guard the emitted nonce is always the one incremented at the top of the function:
   ```solidity
   currentOriginNonce += 1;
   uint64 nonce = currentOriginNonce;   // capture before external call
   ...
   safeTransferFrom(...);
   ...
   initTransferExtension(..., nonce, ...);
   emit InitTransfer(..., nonce, ...);
   ```

3. **NEAR-side defense (belt-and-suspenders):** Track used EVM `(chain, origin_nonce)` pairs in `fin_transfer_callback` and reject any proof whose origin nonce has already been finalized.

---

### Proof of Concept

```
// Attacker contract (simplified)
contract AttackERC777 is ERC777 {
    OmniBridge bridge;
    IERC20 legitimateToken;
    uint128 stealAmount;

    function tokensToSend(address, address, address, uint256, bytes memory, bytes memory) external override {
        // Reentrant call with legitimate token
        legitimateToken.approve(address(bridge), stealAmount);
        bridge.initTransfer(address(legitimateToken), stealAmount, 0, 0, "attacker.near", "");
        // currentOriginNonce is now N+1 inside this reentrant call
        // emit InitTransfer(legitimateToken, N+1, stealAmount)
    }
}

// Attack sequence:
// 1. Deploy AttackERC777 and fund attacker with stealAmount of legitimateToken
// 2. Call bridge.initTransfer(attackERC777, 1, 0, 0, "attacker.near", "")
//    → currentOriginNonce = N
//    → safeTransferFrom fires tokensToSend hook
//      → reentrant initTransfer: currentOriginNonce = N+1
//      → emit InitTransfer(legitimateToken, N+1, stealAmount)
//    → outer call resumes, reads currentOriginNonce = N+1
//    → emit InitTransfer(attackERC777, N+1, 1)
// 3. Relayer submits both proofs to NEAR
// 4. NEAR mints stealAmount of bridged legitimateToken TWICE
//    → attacker receives 2 * stealAmount on NEAR for 1 * stealAmount locked on EVM
```

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L28-34)
```text
contract OmniBridge is
    UUPSUpgradeable,
    AccessControlUpgradeable,
    SelectivePausableUpgradable,
    IERC1155Receiver
{
    using SafeERC20 for IERC20;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L381-381)
```text
        currentOriginNonce += 1;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L407-411)
```text
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L415-436)
```text
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
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L448-490)
```text
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
