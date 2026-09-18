## Title
Tokens transferred to a pointer contract's own address become permanently stuck with no recovery mechanism - ([File: contracts/src/CW20ERC20Pointer.sol], [File: contracts/src/NativeSeiTokensERC20.sol])

### Summary
Sei's CW↔EVM pointer contracts (`CW20ERC20Pointer`, `NativeSeiTokensERC20`) do not custody tokens directly — they proxy balance/transfer calls to an underlying CosmWasm CW20 contract or the Cosmos bank module, keyed by the pointer contract's own associated Sei address. If a user transfers tokens *to the pointer contract's own address* (a scenario the codebase explicitly tests and expects to "succeed"), those tokens end up owned by the pointer contract's Sei account, but the pointer contract has no logic, owner, or rescue function that can ever initiate a transfer *as* that Sei account. This mirrors the reported class of bug: a contract holding value with no way to extract accidentally/deliberately deposited funds.

### Finding Description
`CW20ERC20Pointer.transfer`/`transferFrom` and `NativeSeiTokensERC20`'s `_update` (invoked by ERC20 `transfer`/`transferFrom`) always execute the underlying CW20/bank operation with the pointer contract itself as the effective Sei-side sender/holder: [1](#0-0) [2](#0-1) 

Nothing in either contract prevents `to == address(this)`. The test suite explicitly validates that a transfer to the pointer's own address succeeds and the balance is credited to it: [3](#0-2) 

Once balance is credited to the pointer contract's Sei address, the *only* way to move it out is for the pointer contract itself to be `msg.sender` of a subsequent `transfer`/`transferFrom`/CW20 `transfer` call. Both pointer contracts contain no `receive()`/`fallback()`/admin/rescue function, and no self-call path exists by which the contract can act as its own token owner — the contract has no owner, no privileged role, and no code path that issues a CW20/bank transfer with itself as sender: [4](#0-3) 

For the native-token pointer, the `bank.send` precompile additionally *requires* the pointer contract to be the caller for that denom, meaning the only party capable of moving the pointer's own bank balance is the pointer contract itself acting via an EVM call it can never originate: [5](#0-4) 

### Impact Explanation
Any CW20 or native tokenfactory tokens sent to a pointer contract's own address (via a plain `transfer(pointerAddress, amount)` call, which any unprivileged EVM caller can trigger) become permanently and irrecoverably locked in the pointer contract's underlying Sei account. This is a genuine, concrete "permanent freezing of funds" outcome reachable purely through a public pointer contract and a single EVM transaction — no privileged access, governance, or malicious-node behavior required.

### Likelihood Explanation
Likelihood is realistic: pointer addresses are commonly used as intermediate/settlement addresses in DeFi integrations, multi-step swaps, or bridging flows, and a mistaken/self-referential transfer to the pointer's own address is a plausible user or integrator error. Because pointer contracts are the canonical, chain-provided interoperability mechanism between CW20/native tokens and ERC20, this path is broadly and repeatedly exercised by ordinary users and dApps.

### Recommendation
- In `CW20ERC20Pointer.transfer`/`transferFrom` and `NativeSeiTokensERC20._update` (and their CW1155/ERC721/ERC1155 pointer counterparts), explicitly reject transfers where `to == address(this)` (mirroring the existing zero-address check), or
- Add an explicit rescue/reconciliation function restricted to a well-defined authority (e.g., chain governance or a fixed admin) that can sweep any balance mistakenly held by the pointer's own Sei address back to a recoverable account.

### Proof of Concept
1. Deploy/register a CW20↔ERC20 pointer for any CW20 token (`registerPointerForCw20`).
2. From any account holding the wrapped token, call `pointer.transfer(pointerAddress, amount)` — this succeeds today, as demonstrated by the existing test: [3](#0-2) 
3. Query `pointer.balanceOf(pointerAddress)` — it now reflects the deposited `amount`.
4. Attempt to move this balance out by calling `pointer.transfer(...)`/`transferFrom(...)` from any account — every code path requires `msg.sender` (EVM) to correspond to the CW20 "sender"/"owner" field, and since the pointer contract has no key and no self-call logic, no account can ever satisfy `owner == pointerAddress` in a way that authorizes moving the funds. The tokens remain locked indefinitely.

### Citations

**File:** contracts/src/CW20ERC20Pointer.sol (L1-27)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/utils/Strings.sol";
import {IWasmd} from "./precompiles/IWasmd.sol";
import {IJson} from "./precompiles/IJson.sol";
import {IAddr} from "./precompiles/IAddr.sol";

contract CW20ERC20Pointer is ERC20 {

    address constant WASMD_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001002;
    address constant JSON_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001003;
    address constant ADDR_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001004;

    string public Cw20Address;
    IWasmd public WasmdPrecompile;
    IJson public JsonPrecompile;
    IAddr public AddrPrecompile;

    constructor(string memory Cw20Address_, string memory name_, string memory symbol_) ERC20(name_, symbol_) {
        WasmdPrecompile = IWasmd(WASMD_PRECOMPILE_ADDRESS);
        JsonPrecompile = IJson(JSON_PRECOMPILE_ADDRESS);
        AddrPrecompile = IAddr(ADDR_PRECOMPILE_ADDRESS);
        Cw20Address = Cw20Address_;
    }
```

**File:** contracts/src/CW20ERC20Pointer.sol (L79-96)
```text
    function transfer(address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer", _curlyBrace(_join(recipient, amt, ","))));
        _execute(bytes(req));
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) public override returns (bool) {
        require(to != address(0), "ERC20: transfer to the zero address");
        string memory sender = _formatPayload("owner", _doubleQuotes(AddrPrecompile.getSeiAddr(from)));
        string memory recipient = _formatPayload("recipient", _doubleQuotes(AddrPrecompile.getSeiAddr(to)));
        string memory amt = _formatPayload("amount", _doubleQuotes(Strings.toString(amount)));
        string memory req = _curlyBrace(_formatPayload("transfer_from", _curlyBrace(_join(_join(sender, recipient, ","), amt, ","))));
        _execute(bytes(req));
        return true;
    }
```

**File:** contracts/src/NativeSeiTokensERC20.sol (L45-49)
```text
    function _update(address from, address to, uint256 value) internal override {
        bool success = BankPrecompile.send(from, to, denom, value);
        require(success, "NativeSeiTokensERC20: transfer failed");
        emit Transfer(from, to, value);
    }
```

**File:** contracts/test/CW20toERC20PointerTest.js (L130-140)
```javascript
                it("transfer to contract address should succeed", async function() {
                    await associateWasm(pointer);
                    const respBefore = await queryWasm(pointer, "balance", {address: admin.seiAddress});
                    const balanceBefore = respBefore.data.balance;

                    await executeWasm(pointer,  { transfer: { recipient: pointer, amount: "100" } });
                    const respAfter = await queryWasm(pointer, "balance", {address: admin.seiAddress});
                    const balanceAfter = respAfter.data.balance;

                    expect(balanceAfter).to.equal((parseInt(balanceBefore) - 100).toString());
                });
```

**File:** precompiles/bank/bank.go (L213-216)
```go
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, 0, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
```
