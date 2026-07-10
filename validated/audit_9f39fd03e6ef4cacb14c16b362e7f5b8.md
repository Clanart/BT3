### Title
Factory Registry Overwrite Without Pending-Transfer Drain Causes Permanent Fund Lock — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`add_factory` silently overwrites the registered factory address for a chain. Every proof-verification callback validates the emitter address against the live registry. If the DAO rotates the factory (a routine upgrade action) while cross-chain transfers are in flight, those transfers permanently fail the `UnknownFactory` guard and the underlying user funds become irrecoverable.

---

### Finding Description

`add_factory` is the sole write path for the `factories` map:

```rust
// near/omni-bridge/src/lib.rs  line 1501-1504
#[access_control_any(roles(Role::DAO))]
pub fn add_factory(&mut self, address: OmniAddress) {
    self.factories.insert(&(&address).into(), &address);   // silent overwrite
}
```

`insert` on a `LookupMap` returns the old value and overwrites unconditionally. There is no guard that checks whether pending transfers still reference the old address, and there is no `remove_factory` or `drain_pending_transfers` helper.

Four callbacks enforce the invariant `factories[chain] == emitter_address`:

**`fin_transfer_callback`** — finalises an inbound transfer (source chain → NEAR):
```rust
// lines 708-713
require!(
    self.factories
        .get(&init_transfer.emitter_address.get_chain())
        == Some(init_transfer.emitter_address),
    BridgeError::UnknownFactory.as_ref()
);
```

**`claim_fee_callback`** — lets a relayer collect its fee after a NEAR-originated transfer is settled on the destination chain:
```rust
// lines 1087-1092
require!(
    self.factories
        .get(&fin_transfer.emitter_address.get_chain())
        == Some(fin_transfer.emitter_address),
    BridgeError::UnknownFactory.as_ref()
);
// remove_transfer_message is called AFTER this check (line 1094)
let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
```

`deploy_token_callback` (lines 1160-1163) and `bind_token_callback` (lines 1253-1258) carry the identical guard.

The same structural problem exists for `remove_prover`:

```rust
// lines 1754-1757
#[access_control_any(roles(Role::DAO))]
pub fn remove_prover(&mut self, chain: ChainKind) {
    self.provers.remove(&chain);
}
```

`verify_proof` (lines 2755-2767) panics immediately if the prover is absent, so any `fin_transfer` or `claim_fee` call submitted after prover removal fails before the cross-contract call is even dispatched.

---

### Impact Explanation

**Scenario A — inbound transfer (source chain → NEAR), factory rotated mid-flight:**

1. User calls `initTransfer` on the EVM `OmniBridge` → tokens are locked in the EVM contract.
2. DAO calls `add_factory` with the new EVM bridge address (e.g., after a contract upgrade), overwriting the old address.
3. Relayer submits `fin_transfer` on NEAR with a proof whose `emitter_address` is the *old* EVM bridge.
4. `fin_transfer_callback` panics at the `UnknownFactory` require (line 708-713).
5. The NEAR transaction reverts; the EVM bridge has no refund path. User tokens are permanently frozen.

**Impact class:** Critical — irrecoverable lock of user funds in the EVM bridge.

**Scenario B — outbound transfer (NEAR → destination chain), factory rotated before fee claim:**

1. User sends tokens via `ft_on_transfer` → `init_transfer`; the full amount (including relayer fee) is locked in `pending_transfers`.
2. The transfer is signed and settled on the destination chain; the recipient receives `amount − fee`.
3. DAO rotates the factory for the destination chain.
4. Relayer calls `claim_fee`; `claim_fee_callback` panics at the `UnknownFactory` require (lines 1087-1092) *before* `remove_transfer_message` is reached.
5. The transfer entry stays in `pending_transfers` indefinitely; the fee portion is permanently stuck in the NEAR bridge.

**Impact class:** High — unclaimable settlement / stuck protocol funds.

---

### Likelihood Explanation

Factory rotation is a routine operational event (EVM bridge upgrade, chain migration, security patch). The window between a factory update and the processing of all in-flight proofs is non-zero on any live network. No malicious intent is required; the DAO acting in good faith triggers the condition. The analogous pattern in the reference report (pool delisting before `burnFees`) was confirmed Medium severity for the same reason.

---

### Recommendation

1. **Before rotating a factory**, require that no `pending_transfers` reference the old factory's chain, or provide a DAO-callable `drain_pending_transfers(chain_kind)` that processes or cancels them first.
2. Alternatively, store the *set* of historically valid factory addresses per chain and validate against the set rather than a single current value, so old proofs remain processable after a rotation.
3. Apply the same pattern to `remove_prover`: gate removal on zero pending transfers for that chain, or provide a migration path.

---

### Proof of Concept

```
// Scenario A — permanent EVM fund lock

1. Alice calls OmniBridge.initTransfer(tokenAddress, 1000, ...) on Ethereum.
   → 1000 tokens locked in EVM OmniBridge at address OLD_FACTORY.

2. DAO calls near_bridge.add_factory({ address: "eth:NEW_FACTORY" })
   → factories[Eth] = NEW_FACTORY  (OLD_FACTORY silently discarded)

3. Relayer calls near_bridge.fin_transfer(chain_kind=Eth, prover_args=<proof>)
   → verify_proof succeeds (prover still registered, proof is cryptographically valid)
   → fin_transfer_callback fires:
        require!(
            self.factories.get(&Eth) == Some(OLD_FACTORY)  // ← false, now NEW_FACTORY
        )  → panics with ERR_UNKNOWN_FACTORY

4. NEAR tx reverts. EVM bridge has no refund entrypoint.
   Alice's 1000 tokens are permanently locked.
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** near/omni-bridge/src/lib.rs (L708-713)
```rust
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1087-1094)
```rust
        require!(
            self.factories
                .get(&fin_transfer.emitter_address.get_chain())
                == Some(fin_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
```

**File:** near/omni-bridge/src/lib.rs (L1501-1504)
```rust
    #[access_control_any(roles(Role::DAO))]
    pub fn add_factory(&mut self, address: OmniAddress) {
        self.factories.insert(&(&address).into(), &address);
    }
```

**File:** near/omni-bridge/src/lib.rs (L1754-1757)
```rust
    #[access_control_any(roles(Role::DAO))]
    pub fn remove_prover(&mut self, chain: ChainKind) {
        self.provers.remove(&chain);
    }
```

**File:** near/omni-bridge/src/lib.rs (L2755-2767)
```rust
    fn verify_proof(&self, chain_kind: ChainKind, prover_args: Vec<u8>) -> Promise {
        let prover_account_id = self.provers.get(&chain_kind).unwrap_or_else(|| {
            env::panic_str(
                BridgeError::ProverForChainKindNotRegistered
                    .to_string()
                    .as_str(),
            )
        });

        ext_omni_prover_proxy::ext(prover_account_id)
            .with_static_gas(VERIFY_PROOF_GAS)
            .with_attached_deposit(NearToken::from_near(0))
            .verify_proof(prover_args)
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
