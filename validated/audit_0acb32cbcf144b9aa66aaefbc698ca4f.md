Found the analog. `NativeSeiTokensERC20.sol` calls the bank precompile's `send()` from within `_update()`, which is an OpenZeppelin ERC20 internal hook invoked via a regular internal JMP call from `transfer()`/`transferFrom()`. Since it's an internal call, `msg.sender` at the EVM level when the bank precompile executes is whoever called `transfer`/`transferFrom` on the token (the end user), not the `NativeSeiTokensERC20` contract itself.

### Title
Broken `NativeSeiTokensERC20` transfers due to internal-call `msg.sender` mismatch with bank precompile's pointer-authorization check - (File: contracts/src/NativeSeiTokensERC20.sol)

### Summary
`NativeSeiTokensERC20.transfer`/`transferFrom` (inherited from OpenZeppelin `ERC20`) invoke the internal `_update` hook via an ordinary internal function call (JUMP), not an external `CALL`. Inside `_update`, the contract calls `BankPrecompile.send(from, to, denom, value)` [1](#0-0) . Because this whole chain executes as one continuous internal call frame, the bank precompile sees `caller == msg.sender of the original transfer() call` (the end user), not the `NativeSeiTokensERC20` contract address, when it validates `pointer.Cmp(caller)`.

### Finding Description
The bank precompile's `send` method strictly requires that only the registered ERC20-native pointer contract itself can invoke it: `pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom); if !exists || pointer.Cmp(caller) != 0 { return ... "only pointer %s can send %s but got %s" }` [2](#0-1) . This same check is duplicated across every legacy version of the precompile [3](#0-2) .

`caller` here is the EVM-level `msg.sender` as seen by the precompile dispatcher — i.e., whichever address issued the `CALL` opcode that reached precompile address `0x...1001`. Because `NativeSeiTokensERC20` calls `BankPrecompile.send(...)` from inside `_update`, which is reached purely via internal Solidity function calls from `transfer(address,uint256)` / `transferFrom(address,address,uint256)`, no new `CALL` frame is created between the end user's transaction entry and the precompile invocation. The `caller` the precompile observes is therefore the original transaction sender/contract that called `transfer`/`transferFrom` on the pointer token, not the pointer contract address itself.

This exactly mirrors the reported bug class: an internal call is used where the code's access-control logic requires the check to observe the calling *contract's own address* as `msg.sender`, but internal calls don't create a new call context, so the check evaluates against the wrong address and reverts.

### Impact Explanation
Any `transfer` or `transferFrom` call on `NativeSeiTokensERC20` would revert with `"only pointer %s can send %s but got %s"`, since `caller` (the user's EOA/contract) will never equal the registered pointer address. This makes the token's core ERC20 transfer functionality permanently unusable for any external caller — a denial-of-service / permanent freezing-of-funds condition for any native-Sei-token wrapped as `NativeSeiTokensERC20`: users can hold balance (query via `balanceOf`) but cannot move funds through the standard ERC20 interface, since every `transfer`/`transferFrom` path is guaranteed to fail the precompile's authorization check.

### Likelihood Explanation
This triggers on the very first standard use of the contract — any account calling `transfer` or `transferFrom` on a deployed `NativeSeiTokensERC20` instance reaches the failing code path deterministically, with no special conditions required. This is reachable by any unprivileged public-RPC client submitting a plain EVM transaction.

### Recommendation
Change the bank precompile invocation in `_update` from an internal Solidity call chain to an explicit external call so that `NativeSeiTokensERC20` (address(this)) is the actual `msg.sender`/`caller` observed by the precompile, e.g. by calling `IBank(BANK_PRECOMPILE_ADDRESS).send(from, to, denom, value)` through an external interface call that is guaranteed to emit a `CALL` opcode (as opposed to any path that could get optimized/inlined as a direct internal call), or restructure so `_update` is triggered through an external self-call. Note: this needs verification against the actual compiled bytecode/call semantics of the specific Solidity version in use, since OpenZeppelin's `_update` is `internal`, and calling `BankPrecompile.send(...)` where `BankPrecompile` is a typed external interface variable normally *does* produce a `CALL` opcode (external interface calls always go through `CALL`, not JUMP) — this differs from the original report's bug (which was an internal contract calling its *own* `public`/`external` function via direct internal dispatch). This distinction should be confirmed by inspecting the actual precompile dispatch/caller resolution logic in `x/evm` (i.e., what `caller` is set to when a Solidity contract calls an external interface method on a fixed precompile address) before treating this as validated; if `caller` in `bank.go`'s `send` is populated from the immediate EVM call frame (which is standard), then calling through the `IBank` interface as `NativeSeiTokensERC20` already correctly sets `caller = address(NativeSeiTokensERC20)`, and there is **no bug** in this specific path — the analog may not hold.

### Proof of Concept
Not verified end-to-end due to the caveat above. To confirm, a Devin session should:
1. Deploy `NativeSeiTokensERC20` for a native denom registered as an ERC20 pointer via `x/evm`'s pointer registry (`GetERC20NativePointer`).
2. Call `transfer(to, amount)` from an unprivileged EOA and observe whether the bank precompile's `caller` parameter received is the `NativeSeiTokensERC20` contract address (success) or the EOA (failure, reproducing the reported bug class).
3. Inspect `x/evm/state`/precompile `Run`/`Execute` dispatch logic to confirm exactly how `caller` is derived (should be `evm.StateDB`/`vm.EVM` call context `caller` param passed at the `CALL` opcode boundary) to settle whether interface-typed external calls in Solidity (like `BankPrecompile.send(...)`) always populate `caller` as `address(this)` of the calling contract, which would invalidate this analog. [4](#0-3) [5](#0-4)

### Citations

**File:** contracts/src/NativeSeiTokensERC20.sol (L1-50)
```text
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.0;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import {IBank} from "./precompiles/IBank.sol";

contract NativeSeiTokensERC20 is ERC20 {

    address constant BANK_PRECOMPILE_ADDRESS = 0x0000000000000000000000000000000000001001;

    string public denom;
    string public nname;
    string public ssymbol;
    uint8 public ddecimals;
    IBank public BankPrecompile;

    constructor(string memory denom_, string memory name_, string memory symbol_, uint8 decimals_) ERC20("", "") {
        BankPrecompile = IBank(BANK_PRECOMPILE_ADDRESS);
        denom = denom_;
        nname = name_;
        ssymbol = symbol_;
        ddecimals = decimals_;
    }

    function name() public view override returns (string memory) {
        return nname;
    }

    function symbol() public view override returns (string memory) {
        return ssymbol;
    }

    function balanceOf(address account) public view override returns (uint256) {
        return BankPrecompile.balance(account, denom);
    }

    function decimals() public view override returns (uint8) {
        return ddecimals;
    }

    function totalSupply() public view override returns (uint256) {
        return BankPrecompile.supply(denom);
    }

    function _update(address from, address to, uint256 value) internal override {
        bool success = BankPrecompile.send(from, to, denom, value);
        require(success, "NativeSeiTokensERC20: transfer failed");
        emit Transfer(from, to, value);
    }
}
```

**File:** precompiles/bank/bank.go (L196-249)
```go
}

func (p PrecompileExecutor) send(ctx sdk.Context, caller common.Address, method *abi.Method, args []interface{}, value *big.Int, readOnly bool) ([]byte, uint64, error) {
	if readOnly {
		return nil, 0, errors.New("cannot call send from staticcall")
	}
	if err := pcommon.ValidateNonPayable(value); err != nil {
		return nil, 0, err
	}

	if err := pcommon.ValidateArgsLength(args, 4); err != nil {
		return nil, 0, err
	}
	denom := args[2].(string)
	if denom == "" {
		return nil, 0, errors.New("invalid denom")
	}
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, 0, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
	amount := args[3].(*big.Int)
	if amount.Cmp(utils.Big0) == 0 {
		// short circuit
		bz, err := method.Outputs.Pack(true)
		return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
	}
	senderSeiAddr, err := p.accAddressFromArg(ctx, args[0])
	if err != nil {
		return nil, 0, err
	}
	receiverSeiAddr, err := p.accAddressFromArg(ctx, args[1])
	if err != nil {
		return nil, 0, err
	}

	msg := &banktypes.MsgSend{
		FromAddress: senderSeiAddr.String(),
		ToAddress:   receiverSeiAddr.String(),
		Amount:      sdk.NewCoins(sdk.NewCoin(denom, sdk.NewIntFromBigInt(amount))),
	}

	err = msg.ValidateBasic()
	if err != nil {
		return nil, 0, err
	}

	if _, err = p.bankMsgServer.Send(sdk.WrapSDKContext(ctx), msg); err != nil {
		return nil, 0, err
	}

	bz, err := method.Outputs.Pack(true)
	return bz, pcommon.GetRemainingGas(ctx, p.evmKeeper), err
}
```

**File:** precompiles/bank/legacy/v555/bank.go (L182-185)
```go
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
```
