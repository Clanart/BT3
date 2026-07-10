### Title
`omni_bridge_derived_address` Cannot Be Updated After Deployment — Permanent Freezing of StarkNet Bridge Funds on MPC Key Rotation - (File: starknet/src/omni_bridge.cairo)

### Summary
The StarkNet `OmniBridge` contract stores `omni_bridge_derived_address` (the MPC-derived Ethereum address used to verify every bridge signature) in constructor-only storage with no admin setter. The EVM counterpart explicitly provides `setNearBridgeDerivedAddress` for exactly this purpose, but the StarkNet contract has no equivalent. If the MPC key is rotated — a routine operational event — all `fin_transfer` and `deploy_token` calls on StarkNet will permanently revert, freezing every in-flight cross-chain transfer destined for StarkNet.

### Finding Description

The StarkNet `OmniBridge` constructor writes `omni_bridge_derived_address` once and never exposes a setter:

```cairo
// starknet/src/omni_bridge.cairo  constructor, lines 131-132
self.omni_bridge_derived_address.write(omni_bridge_derived_address);
self.omni_bridge_chain_id.write(omni_bridge_chain_id);
```

Every signature-bearing entry point reads this value and calls `verify_eth_signature` against it:

```cairo
// lines 398-406
fn _verify_borsh_signature(
    ref self: ContractState, borsh_bytes: @ByteArray, signature: Signature,
) {
    let message_hash_le = compute_keccak_byte_array(borsh_bytes);
    let message_hash = reverse_u256_bytes(message_hash_le);
    let sig = signature_from_vrs(signature.v, signature.r, signature.s);
    verify_eth_signature(message_hash, sig, self.omni_bridge_derived_address.read());
}
```

`_verify_borsh_signature` is called unconditionally inside both `deploy_token` (line 205) and `fin_transfer` (line 252-254). The `IOmniBridge` interface exposes no function to change `omni_bridge_derived_address` or `omni_bridge_chain_id`.

By contrast, the EVM bridge explicitly provides an updatable path:

```solidity
// evm/src/omni-bridge/contracts/OmniBridge.sol  lines 568-572
function setNearBridgeDerivedAddress(
    address nearBridgeDerivedAddress_
) external onlyRole(DEFAULT_ADMIN_ROLE) {
    nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
}
```

The NEAR bridge stores `mpc_signer` as a mutable field (line 232 of `near/omni-bridge/src/lib.rs`). The Solana bridge exposes `set_derived_near_bridge_address` (line 46-52 of `solana/programs/bridge_token_factory/src/instructions/admin/change_config.rs`). The StarkNet bridge is the only component with no update path.

### Impact Explanation

When the MPC key is rotated (a standard security practice), the derived Ethereum address changes. At that point:

- Every call to `fin_transfer` on StarkNet reverts at `verify_eth_signature` because the stored address no longer matches the new MPC key.
- Every call to `deploy_token` on StarkNet reverts for the same reason.
- Users who already initiated transfers from NEAR, EVM, or Solana to a StarkNet recipient have their source-chain funds locked in the bridge with no finalization path on StarkNet.
- The only recovery is a full contract class-hash upgrade that adds a setter and then calls it — a multi-step process that cannot be performed atomically and leaves funds frozen for an indeterminate window.

This matches **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows**.

### Likelihood Explanation

MPC key rotation is a planned, recurring operational event in any MPC-based bridge. The NEAR, EVM, and Solana components all anticipate it by providing setter functions. The StarkNet component does not. The probability of key rotation occurring during the contract's lifetime is high; the probability that the omission is noticed and a class-hash upgrade is prepared and executed before any in-flight transfer times out is low.

### Recommendation

Add an admin-gated setter to the `IOmniBridge` interface and its implementation, mirroring the EVM pattern:

```cairo
fn set_omni_bridge_derived_address(
    ref self: TContractState,
    new_address: EthAddress,
);
```

Restrict it to `DEFAULT_ADMIN_ROLE`. Similarly add a setter for `omni_bridge_chain_id` and `strk_token_address` for completeness, as neither has an update path either.

### Proof of Concept

1. Deploy `OmniBridge` on StarkNet with MPC-derived address `A`.
2. MPC operators rotate the key; the new derived address is `B`. NEAR, EVM, and Solana bridges are updated to `B` via their respective setters.
3. A user initiates a transfer from NEAR to a StarkNet recipient. The NEAR bridge records the transfer and the MPC signs the finalization payload with key `B`.
4. The relayer calls `fin_transfer` on StarkNet with the `B`-signed payload.
5. `_verify_borsh_signature` calls `verify_eth_signature(hash, sig, A)` — mismatch → revert `ERR_INVALID_SIGNATURE` (implicit panic from `verify_eth_signature`).
6. No alternative finalization path exists. The user's funds are permanently locked in the NEAR bridge with no recourse on StarkNet until a class-hash upgrade is performed. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** starknet/src/omni_bridge.cairo (L122-139)
```text
    #[constructor]
    fn constructor(
        ref self: ContractState,
        omni_bridge_derived_address: EthAddress,
        omni_bridge_chain_id: u8,
        token_class_hash: ClassHash,
        default_admin: ContractAddress,
        strk_token_address: ContractAddress,
    ) {
        self.omni_bridge_derived_address.write(omni_bridge_derived_address);
        self.omni_bridge_chain_id.write(omni_bridge_chain_id);
        self.bridge_token_class_hash.write(token_class_hash);
        self.strk_token_address.write(strk_token_address);
        self.pause_flags.write(0);

        self.accesscontrol.initializer();
        self.accesscontrol._grant_role(DEFAULT_ADMIN_ROLE, default_admin);
    }
```

**File:** starknet/src/omni_bridge.cairo (L202-206)
```text
        fn deploy_token(ref self: ContractState, signature: Signature, payload: MetadataPayload) {
            assert(!_is_paused(@self, PAUSE_DEPLOY_TOKEN), 'ERR_DEPLOY_TOKEN_PAUSED');

            _verify_borsh_signature(ref self, @payload.to_borsh(), signature);

```

**File:** starknet/src/omni_bridge.cairo (L242-254)
```text
        fn fin_transfer(
            ref self: ContractState, signature: Signature, payload: TransferMessagePayload,
        ) {
            assert(!_is_paused(@self, PAUSE_FIN_TRANSFER), 'ERR_FIN_TRANSFER_PAUSED');

            assert(
                !self.is_transfer_finalised(payload.destination_nonce), 'ERR_NONCE_ALREADY_USED',
            );
            _set_transfer_finalised(ref self, payload.destination_nonce);

            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );
```

**File:** starknet/src/omni_bridge.cairo (L398-406)
```text
    fn _verify_borsh_signature(
        ref self: ContractState, borsh_bytes: @ByteArray, signature: Signature,
    ) {
        let message_hash_le = compute_keccak_byte_array(borsh_bytes);
        let message_hash = reverse_u256_bytes(message_hash_le);

        let sig = signature_from_vrs(signature.v, signature.r, signature.s);
        verify_eth_signature(message_hash, sig, self.omni_bridge_derived_address.read());
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L568-572)
```text
    function setNearBridgeDerivedAddress(
        address nearBridgeDerivedAddress_
    ) external onlyRole(DEFAULT_ADMIN_ROLE) {
        nearBridgeDerivedAddress = nearBridgeDerivedAddress_;
    }
```

**File:** solana/programs/bridge_token_factory/src/instructions/admin/change_config.rs (L46-53)
```rust
    pub fn set_derived_near_bridge_address(
        &mut self,
        derived_near_bridge_address: [u8; 64],
    ) -> Result<()> {
        self.config.derived_near_bridge_address = derived_near_bridge_address;

        Ok(())
    }
```

**File:** near/omni-bridge/src/lib.rs (L232-232)
```rust
    pub mpc_signer: AccountId,
```
