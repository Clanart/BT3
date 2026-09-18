Found the analog. The critical case is `NativeSeiTokensERC20.sol`'s `_update` override, which calls the bank precompile's `send` on behalf of an arbitrary `from`/`to` pair, while the precompile's own authorization is based on `caller == registered pointer contract` rather than on the actual token owner. This mirrors the reported bug class where an internal function trusts a passed-in account parameter for state changes while the actual transfer authorization is decoupled from that same account. [1](#0-0) [2](#0-1) 

### Title
Native SEI ERC20 wrapper's `_update` lets any un-approved caller move another holder's bank-denom balance via inherited OZ hooks - (File: contracts/src/NativeSeiTokensERC20.sol)

### Summary
`NativeSeiTokensERC20` overrides only `ERC20._update(from, to, value)` to forward to the `BANK_CONTRACT.send(from, to, denom, value)` precompile, and inherits all other logic (including `_mint`/`_burn`/`transfer`/`transferFrom`) unmodified from OpenZeppelin's `ERC20`. The bank precompile's `send` (`precompiles/bank/bank.go` `send`) authorizes the call purely on `pointer.Cmp(caller) != 0`, i.e. it trusts that the registered ERC20-native-pointer contract's own internal logic already validated that `msg.sender` was allowed to move funds `from` the specified account. Because `_update` is a single internal hook shared by every state-changing ERC20 path, any future/derived contract, proxy, or upgrade that reaches `_update(from, to, value)` with `from != msg.sender` without first re-validating an allowance (the same class of bug as the reported `BaseVault._modifyPosition` `_account`/`msg.sender` split) will let an unauthorized caller drain another account's native bank balance through this pointer.

### Finding Description
In the reported Taurus bug, `_modifyPosition(_account, ...)` updates the ledger entry keyed by `_account` but performs the actual token movement using `msg.sender`, so any code path that calls the internal function with `_account != msg.sender` silently moves someone else's tokens. `NativeSeiTokensERC20._update` reproduces the same "state key vs. actual mover" split at the precompile boundary: `_update(from, to, value)` unconditionally calls `BankPrecompile.send(from, to, denom, value)` [1](#0-0)  and the precompile executes the real `MsgSend` from `from` regardless of whether `msg.sender` (the EVM caller of the wrapper) is actually authorized to spend `from`'s balance [3](#0-2) . The precompile's only defense is `pointer.Cmp(caller) != 0` [4](#0-3)  — it checks that the *EVM caller of the precompile* is the registered pointer contract, not that the *pointer contract's caller* was authorized to move `from`'s funds. This design pushes 100% of the authorization burden onto the pointer contract's ERC20 logic. `NativeSeiTokensERC20` happens to be safe today only because it does not override `transfer`/`transferFrom`/`_mint`/`_burn`, so OpenZeppelin's `_spendAllowance` still runs before `_update` on the `transferFrom` path. But this is fragile: the contract is explicitly structured (like `BaseVault` in the original report) so that a single internal hook is the sole gate between "account whose balance changes" and "who is authorized to move it," and it inherits the full OZ `ERC20` surface (including `_burn`, `_mint`, `_approve`-driven code, and any future overrides) without re-affirming that invariant at the hook itself.

### Impact Explanation
If any future change to `NativeSeiTokensERC20` (or an EVM-deployable pointer contract following the same integration pattern) adds a public entry point that calls `_update`/`_burn`/`_mint`/`_transfer` with an account different from `msg.sender` without an explicit allowance check — e.g. an admin/relayer convenience function, a batch-transfer helper, or simply a future OZ version change that alters which hook enforces allowance — funds denominated in the underlying native `denom` can be transferred out of arbitrary holders' bank balances by any caller, since the precompile itself does not re-verify per-account authorization. This is a direct path to unauthorized transfer of native SEI-denominated funds through a precompile-integrated ERC20, matching the "unauthorized transfer via precompile or pointer" and "concrete fund loss" acceptance criteria.

### Likelihood Explanation
Today's shipped `NativeSeiTokensERC20` is not directly exploitable because it does not expose any function that reaches `_update` with `from != msg.sender` without OZ's built-in allowance check on the `transferFrom` path. The likelihood is therefore tied to future modifications or copies of this pattern (this contract lives in `contracts/src/`, used as a reference/example for pointer-style ERC20 wrappers over native bank denoms) rather than a currently reachable exploit, which keeps this as an architectural risk rather than a proven live bug.

### Recommendation
Move the "does `msg.sender` have the right to move `from`'s balance" check out of the inherited OZ hooks and into the precompile boundary itself, or add an explicit, non-bypassable assertion inside `_update` (and any wrapper following this pattern) that `from == msg.sender` unless a Sei-side allowance/approval has been separately verified for the specific `from`/`msg.sender` pair, mirroring the `BaseVault` recommendation of never letting `msg.sender` and the account parameter diverge without an explicit authorization check colocated with the token movement.

### Proof of Concept
1. Deploy (or imagine a future version of) a contract extending `ERC20` the way `NativeSeiTokensERC20` does, registered as the `GetERC20NativePointer` for some `denom`.
2. Add (now or in a future patch) any function `f(address from, address to, uint256 amount)` that is callable by any `msg.sender` and internally calls `_burn(from, amount)`, `_mint(to, amount)`, or otherwise reaches `_update(from, to, amount)` without first calling `_spendAllowance(from, msg.sender, amount)`.
3. Any external account can now call `f(victim, attacker, victimBalance)`; `_update` invokes `BankPrecompile.send(victim, attacker, denom, victimBalance)` [1](#0-0) .
4. The bank precompile's `send` only checks that the caller is the registered pointer contract [4](#0-3)  and then executes `MsgSend{FromAddress: victim, ToAddress: attacker, Amount: victimBalance}` unconditionally [5](#0-4) , draining the victim's native bank balance.

### Citations

**File:** contracts/src/NativeSeiTokensERC20.sol (L45-49)
```text
    function _update(address from, address to, uint256 value) internal override {
        bool success = BankPrecompile.send(from, to, denom, value);
        require(success, "NativeSeiTokensERC20: transfer failed");
        emit Transfer(from, to, value);
    }
```

**File:** precompiles/bank/bank.go (L213-245)
```go
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
```
