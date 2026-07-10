The code is fully traceable. Let me map every step precisely.

### Title
Decimal Normalization Mismatch in `deployToken` Permanently Freezes Any Token With `decimals > 18` — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`deployToken` normalizes the EVM-side token decimals to 18 via `_normalizeDecimals`, but passes the **raw** `metadata.decimals` as `originDecimals` to `deployTokenExtension`. The Wormhole message therefore advertises `originDecimals = metadata.decimals` (e.g. 255) while the deployed `BridgeToken` has `decimals() = 18`. NEAR's `bind_token_callback` stores this pair verbatim. Every subsequent call to `normalize_amount` or `denormalize_amount` for that token computes `10_u128.pow(255 - 18)` = `10^237`, which overflows `u128`. Because the NEAR workspace is compiled with `overflow-checks = true`, this is a hard abort — permanently freezing the token.

---

### Finding Description

**Step 1 — Signature check covers raw `metadata.decimals`.**

`deployToken` hashes and verifies the MPC signature over the borsh payload that includes `bytes1(metadata.decimals)`. [1](#0-0) 

The MPC signs whatever the NEAR token registry reports. NEP-141 decimals is a plain `u8`; values above 18 (e.g. 24 for many NEAR tokens, or 255 for a pathological token) are valid and will be signed without any range check.

**Step 2 — EVM token is deployed with normalized decimals, but raw value is forwarded.**

```solidity
uint8 decimals = _normalizeDecimals(metadata.decimals); // caps at 18
...
deployTokenExtension(
    metadata.token,
    bridgeTokenProxy,
    decimals,           // 18
    metadata.decimals   // 255 — raw, un-normalized
);
``` [2](#0-1) 

**Step 3 — Wormhole message encodes the mismatch.**

`OmniBridgeWormhole.deployTokenExtension` publishes:
```
bytes1(decimals)        → 18
bytes1(originDecimals)  → 255
``` [3](#0-2) 

**Step 4 — NEAR stores the corrupted pair.**

`bind_token_callback` passes `deploy_token.decimals = 18` and `deploy_token.origin_decimals = 255` directly into `add_token`, which stores `Decimals { decimals: 18, origin_decimals: 255 }` for the EVM token address. [4](#0-3) [5](#0-4) 

**Step 5 — Every transfer panics due to overflow.**

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into(); // 237
    amount / (10_u128.pow(diff_decimals))  // 10^237 overflows u128 → abort
}
``` [6](#0-5) 

The NEAR workspace is compiled with `overflow-checks = true`: [7](#0-6) 

`u128::pow(237)` overflows `u128` (max ≈ 3.4 × 10^38) and aborts the contract call. The same overflow occurs in `denormalize_amount`. The token is permanently frozen — no transfer can ever complete.

---

### Impact Explanation

Any NEAR token with `decimals > 18` that is deployed on EVM via `deployToken` will have its bridge permanently broken. All `init_transfer` calls on NEAR that invoke `normalize_amount` for that token will abort. All `fin_transfer` callbacks that invoke `denormalize_amount` will abort. Funds already locked in the bridge for that token become irrecoverable. This is a **Critical** permanent freeze.

For tokens with `decimals` only slightly above 18 (e.g. 24), `10^6` fits in `u128` and does not overflow, but the scaling is still wrong: NEAR divides/multiplies by `10^6` on every transfer while the EVM token has the same 18-decimal precision as a token with `origin_decimals = 18`, causing permanent accounting drift and collateralization breakage — a **High** accounting corruption.

---

### Likelihood Explanation

NEAR tokens with `decimals > 18` exist in production (e.g. 24-decimal tokens are common in the NEAR ecosystem). The MPC signs the metadata as reported by the NEAR token registry with no decimals range check. Any relayer holding a valid MPC-signed payload for such a token can trigger this by calling the public `deployToken` function. No privileged access is required beyond possessing the signed payload, which is produced automatically by the bridge protocol.

---

### Recommendation

In `deployToken`, pass the **normalized** value as `originDecimals` as well, or — if the intent is to preserve the original chain's decimals for NEAR-side scaling — do not normalize the EVM token decimals at all and instead handle high-decimal tokens differently. At minimum, add a hard `require(metadata.decimals <= 18)` guard before deployment so the function reverts cleanly rather than silently storing a corrupted decimal pair.

```solidity
// Option A: reject tokens with decimals > 18
require(metadata.decimals <= 18, "ERR_DECIMALS_TOO_HIGH");

// Option B: pass normalized value as originDecimals too
deployTokenExtension(
    metadata.token,
    bridgeTokenProxy,
    decimals,
    decimals   // not metadata.decimals
);
```

---

### Proof of Concept

1. Obtain a valid MPC signature over a `MetadataPayload` with `decimals = 255` (or any value > 18) for a NEAR token that legitimately reports 255 decimals.
2. Call `OmniBridgeWormhole.deployToken(sig, metadata)`.
3. Read the deployed `BridgeToken.decimals()` → returns `18`.
4. Parse the emitted Wormhole VAA payload; the `originDecimals` byte → `255`.
5. On NEAR, call `bind_token` with the VAA; `bind_token_callback` stores `Decimals { decimals: 18, origin_decimals: 255 }`.
6. Attempt any `init_transfer` for this token on NEAR; the call to `normalize_amount` computes `10_u128.pow(237)`, overflows, and aborts — confirming permanent freeze.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L142-153)
```text
        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.Metadata)),
            Borsh.encodeString(metadata.token),
            Borsh.encodeString(metadata.name),
            Borsh.encodeString(metadata.symbol),
            bytes1(metadata.decimals)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L159-179)
```text
        uint8 decimals = _normalizeDecimals(metadata.decimals);

        // slither-disable-next-line reentrancy-no-eth
        address bridgeTokenProxy = address(
            new ERC1967Proxy(
                tokenImplementationAddress,
                abi.encodeWithSelector(
                    BridgeToken.initialize.selector,
                    metadata.name,
                    metadata.symbol,
                    decimals
                )
            )
        );

        deployTokenExtension(
            metadata.token,
            bridgeTokenProxy,
            decimals,
            metadata.decimals
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L54-61)
```text
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.DeployToken)),
            Borsh.encodeString(token),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            bytes1(decimals),
            bytes1(originDecimals)
        );
```

**File:** near/omni-bridge/src/lib.rs (L1262-1267)
```rust
        self.add_token(
            &deploy_token.token,
            &deploy_token.token_address,
            deploy_token.decimals,
            deploy_token.origin_decimals,
        );
```

**File:** near/omni-bridge/src/lib.rs (L2724-2735)
```rust
        require!(
            self.token_decimals
                .insert(
                    token_address,
                    &Decimals {
                        decimals,
                        origin_decimals,
                    }
                )
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```

**File:** near/Cargo.toml (L31-31)
```text
overflow-checks = true
```
