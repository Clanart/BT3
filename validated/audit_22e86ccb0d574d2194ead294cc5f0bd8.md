### Title
Missing Zero-Address Recipient Guard in `ACTION_TRANSFER` Burns `_systemAddress` Pool Tokens — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

---

### Summary

`coreReceiveWithData` in `HyperliquedBridgeToken` decodes the EVM recipient from attacker-controlled `data` bytes and passes it directly to OpenZeppelin's `_update`. When `recipient = address(0)`, `_update(_systemAddress, address(0), amount)` executes as a burn, permanently destroying tokens from the `_systemAddress` pool with no EVM address credited.

---

### Finding Description

In the `ACTION_TRANSFER` branch of `coreReceiveWithData`, the recipient address is decoded from the `tail` of the `data` calldata with no zero-address validation:

```solidity
if (action == ACTION_TRANSFER) {
    address recipient = abi.decode(tail, (address));
    _update(_systemAddress, recipient, amount);   // ← no address(0) guard
}
``` [1](#0-0) 

The `data` bytes originate from HyperCore's `sendToEvmWithData` call, which is a public HyperCore interface. The HyperLiquid system address is a relay — it faithfully forwards user-supplied data to `coreReceiveWithData`. The only access control on the function is `msg.sender == _systemAddress`, which is satisfied by the relay itself: [2](#0-1) 

In OpenZeppelin's `ERC20Upgradeable`, `_update(from, to, value)` with `to == address(0)` is the internal burn path — it decreases `from`'s balance and decreases `totalSupply` without reverting. There is no zero-address check inside `_update` itself (only the higher-level `_transfer` wrapper checks for zero addresses, and that wrapper is not used here). [3](#0-2) 

The `_systemAddress` pool is the canonical mirror of total HyperCore-side balance, seeded by the 3-arg `mint`: [4](#0-3) 

Burning from this pool without crediting any EVM address permanently destroys bridged assets and breaks the HyperCore ↔ HyperEVM accounting invariant.

---

### Impact Explanation

- `totalSupply` decreases by `amount`.
- `_systemAddress` balance decreases by `amount`.
- No EVM address receives tokens.
- The HyperCore-side user's assets are irrecoverably destroyed.
- The pool can be drained to zero across multiple calls, making all subsequent legitimate `ACTION_TRANSFER` calls revert with `ERC20InsufficientBalance`.

This matches **Critical — Permanent freezing / irrecoverable lock of user funds in bridge flows**.

---

### Likelihood Explanation

HyperCore's `sendToEvmWithData` is a public interface. Any HyperCore user can specify `address(0)` as the EVM destination. The system address relays the call without filtering zero-address recipients. The EVM contract performs no validation. The attack is a single HyperCore transaction with no special privileges required.

---

### Recommendation

Add a zero-address guard in the `ACTION_TRANSFER` branch before calling `_update`:

```solidity
if (action == ACTION_TRANSFER) {
    address recipient = abi.decode(tail, (address));
    if (recipient == address(0)) revert InvalidRecipient();
    _update(_systemAddress, recipient, amount);
}
``` [1](#0-0) 

---

### Proof of Concept

```solidity
// 1. Seed the system-address pool (as would happen via 3-arg mint)
token.mint(SYSTEM_ADDRESS, 1000);

// 2. Attacker on HyperCore calls sendToEvmWithData with address(0) as EVM recipient.
//    The system address relays this call:
bytes memory data = abi.encodePacked(
    uint8(0),                              // ACTION_TRANSFER
    abi.encode(address(0))                 // recipient = address(0)
);
vm.prank(SYSTEM_ADDRESS);
token.coreReceiveWithData(attacker, bytes32(0), 0, 1000, 0, data);

// 3. Assert: totalSupply decreased, no address gained balance
assertEq(token.totalSupply(), 0);          // tokens burned
assertEq(token.balanceOf(address(0)), 0);  // address(0) balance not tracked
// Pool is now empty — all future legitimate transfers revert
``` [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L76-83)
```text
    function mint(
        address account,
        uint256 value,
        bytes memory
    ) external override onlyOwner {
        _mint(account, value);
        _update(account, _systemAddress, value);
    }
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L106-141)
```text
    function coreReceiveWithData(
        address from,
        bytes32 /*destinationRecipient*/,
        uint32 /*destinationChainId*/,
        uint256 amount,
        uint64 /*coreNonce*/,
        bytes calldata data
    ) external override {
        if (msg.sender != _systemAddress) revert NotSystemAddress();
        if (data.length == 0) revert EmptyActionData();

        uint8 action = uint8(data[0]);
        bytes calldata tail = data[1:];

        if (action == ACTION_TRANSFER) {
            address recipient = abi.decode(tail, (address));
            _update(_systemAddress, recipient, amount);
        } else if (action == ACTION_INIT_TRANSFER) {
            (uint128 fee, string memory recipient, string memory message) = abi
                .decode(tail, (uint128, string, string));
            uint128 amount128 = amount.toUint128();
            _update(_systemAddress, address(this), amount);
            IOmniBridgeInitTransfer(owner()).initTransfer(
                address(this),
                amount128,
                fee,
                0,
                recipient,
                message
            );
        } else {
            revert UnknownAction(action);
        }

        emit CoreReceived(from, action, amount, data);
    }
```

**File:** evm/src/omni-bridge/contracts/BridgeToken.sol (L4-4)
```text
import {ERC20Upgradeable} from "@openzeppelin/contracts-upgradeable/token/ERC20/ERC20Upgradeable.sol";
```
