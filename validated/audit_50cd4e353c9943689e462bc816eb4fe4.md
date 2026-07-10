### Title
Unchecked Multiplication Overflow in `denormalize_amount` Causes Permanent Fund Freezing or Accounting Corruption — (File: `near/omni-bridge/src/lib.rs`)

### Summary
The NEAR bridge contract's `denormalize_amount` helper performs an unchecked `u128` multiplication that overflows for any token whose `origin_decimals − normalized_decimals ≥ 39`. Because `10_u128.pow(39)` already exceeds `u128::MAX` (~3.4 × 10³⁸), a finalization carrying even a single normalized unit of such a token will either panic-revert (if overflow-checks are on, permanently freezing the already-locked source-chain funds) or silently wrap to a tiny value (if overflow-checks are off, minting a near-zero amount instead of the correct one).

---

### Finding Description

`denormalize_amount` is defined at: [1](#0-0) 

```rust
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount * (10_u128.pow(diff_decimals))   // ← unchecked multiplication
}
```

`normalize_amount` (the inverse, called on the source chain side) is: [2](#0-1) 

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

On the EVM side, `_normalizeDecimals` caps any token's on-chain representation at 18 decimals: [3](#0-2) 

```solidity
function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
    uint8 maxAllowedDecimals = 18;
    if (decimals > maxAllowedDecimals) { return maxAllowedDecimals; }
    return decimals;
}
```

So for any ERC-20 with `origin_decimals = D > 18`, the bridge stores `decimals = 18` and `diff_decimals = D − 18`. When `D ≥ 57`, `diff_decimals ≥ 39`, and `10_u128.pow(39)` already exceeds `u128::MAX` (≈ 3.4 × 10³⁸). The multiplication on line 2778 therefore overflows for **every** finalization of such a token, regardless of the transferred amount.

There is no guard in `deployToken` or `addCustomToken` that rejects tokens with `origin_decimals` high enough to make `diff_decimals ≥ 39`: [4](#0-3) 

---

### Impact Explanation

Two outcomes depending on the Rust release-profile `overflow-checks` setting:

| Overflow-checks | Outcome |
|---|---|
| **Enabled** (panic on overflow) | `denormalize_amount` panics → NEAR finalization reverts → funds already locked/burned on EVM are permanently frozen. **Critical: irrecoverable lock of user funds.** |
| **Disabled** (wrapping, Rust default for release) | `10_u128.pow(diff_decimals)` wraps to a tiny value → NEAR mints a near-zero token amount to the recipient while the full amount was burned on EVM. **High: accounting corruption / broken bridge collateralization.** |

Both outcomes are within the allowed impact scope.

---

### Likelihood Explanation

- Any ERC-20 token with `decimals > 56` (e.g., some rebasing or synthetic tokens) triggers the overflow.
- Token registration requires only a valid MPC signature for the `MetadataPayload`; the `decimals` field is taken directly from the on-chain token metadata with no upper-bound rejection.
- An unprivileged user can initiate the transfer; the overflow fires unconditionally during finalization on NEAR.
- No special privileges, colluding parties, or chain-level attacks are required.

---

### Recommendation

Replace the bare multiplication and exponentiation with checked variants:

```rust
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    let scale = 10_u128
        .checked_pow(diff_decimals)
        .expect("ERR_DECIMAL_SCALE_OVERFLOW");
    amount
        .checked_mul(scale)
        .expect("ERR_DENORMALIZE_OVERFLOW")
}
```

Additionally, enforce an upper bound on `diff_decimals` at token-registration time (both on EVM in `deployToken`/`addCustomToken` and on NEAR) so that only tokens whose full-precision amounts fit in `u128` can be bridged.

---

### Proof of Concept

1. Deploy an ERC-20 token on EVM with `decimals() = 57`.
2. Call `OmniBridge.deployToken(sig, MetadataPayload { decimals: 57, … })`. The bridge stores `origin_decimals = 57`, `decimals = 18`, `diff_decimals = 39`.
3. Approve and call `OmniBridge.initTransfer(token, amount=1e18, fee=0, …)`. The EVM side burns `1e18` raw units (= 1 normalized unit after dividing by `10^39`). The transfer message carries `amount = 1`.
4. On NEAR, the relayer submits the finalization. `denormalize_amount(1, Decimals { origin_decimals: 57, decimals: 18 })` computes `1 * 10_u128.pow(39)`.
5. `10_u128.pow(39)` = 10³⁹ > `u128::MAX` (≈ 3.4 × 10³⁸) → **overflow**.
   - With overflow-checks: NEAR transaction panics; the `1e18` EVM tokens are permanently locked.
   - Without overflow-checks: result wraps to `10^39 mod 2^128` ≈ `2.95 × 10¹¹`; NEAR mints ~295 billion raw units instead of `10^39`, breaking collateralization. [1](#0-0) [3](#0-2)

### Citations

**File:** near/omni-bridge/src/lib.rs (L2776-2779)
```rust
    fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount * (10_u128.pow(diff_decimals))
    }
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L135-195)
```text
    function deployToken(
        bytes calldata signatureData,
        BridgeTypes.MetadataPayload calldata metadata
    ) external payable whenNotPaused(PAUSED_DEPLOY_TOKEN) returns (address) {
        if (tokenImplementationAddress == address(0)) {
            revert TokenImplementationNotSet();
        }
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

        require(
            !isBridgeToken[nearToEthToken[metadata.token]],
            "ERR_TOKEN_EXIST"
        );
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

        emit BridgeTypes.DeployToken(
            bridgeTokenProxy,
            metadata.token,
            metadata.name,
            metadata.symbol,
            decimals,
            metadata.decimals
        );

        isBridgeToken[address(bridgeTokenProxy)] = true;
        ethToNearToken[address(bridgeTokenProxy)] = metadata.token;
        nearToEthToken[metadata.token] = address(bridgeTokenProxy);

        return bridgeTokenProxy;
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L586-592)
```text
    function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
        uint8 maxAllowedDecimals = 18;
        if (decimals > maxAllowedDecimals) {
            return maxAllowedDecimals;
        }
        return decimals;
    }
```
