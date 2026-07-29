### Title
Permissionless registration lets a malicious ERC20 contract mint unbacked native coins in `ConvertERC20` - (File: x/erc20/keeper/msg_server.go, x/erc20/keeper/proposals.go)

### Summary
The `checkSuccess`/inline-assembly bug class describes trusting an externally-controlled contract's self-reported "success" signal (return data) instead of independently verified state. Cosmos EVM's `x/erc20` module contains a direct analog: `ConvertERC20` mints native bank coins based entirely on the return data/logs/`balanceOf` responses produced by an arbitrary, attacker-supplied ERC20 contract, and `RegisterERC20` permits any unprivileged account to register such a contract when `PermissionlessRegistration` is enabled.

### Finding Description
`RegisterERC20` at [1](#0-0)  allows any signer to register an arbitrary ERC20 contract as a token pair when `params.PermissionlessRegistration` is true, with no requirement that the contract be a conforming/audited ERC20 implementation.

`convertERC20IntoCoinsForNativeToken` then relies exclusively on the called contract's own reported state to decide whether to mint native coins: [2](#0-1) 

The function:
1. Calls `contract.transfer(ModuleAddress, amount)` on the attacker-deployed contract via `CallEVMWithData`.
2. If the call returns no data, it falls back to `validateTransferEventExists`, which only checks that a `Transfer` log with the right event-signature topic and emitting address exists — logs are fully attacker-controlled (`emit Transfer(...)` can be called by the contract without ever moving any balance): [3](#0-2) 
3. It re-queries `balanceToken`/`balanceTokenAfter` via `BalanceOf`, which is itself just another call into the same attacker-controlled bytecode — the contract's `balanceOf` can be hard-coded to always report whatever value makes `balanceTokenAfter - balanceToken == amount`, regardless of actual token movement.
4. After these self-reported checks "pass", the module unconditionally calls `k.bankKeeper.MintCoins` and `SendCoinsFromModuleToAccount`, creating real, spendable native coins.

This mirrors the reported bug class precisely: a critical authorization decision (whether to mint value) is derived from data supplied by the very contract being validated, with no independent trust anchor (e.g., verified escrow balance held by a trusted contract, or module-owned token logic). Because a fake ERC20 contract's `transfer`/`balanceOf`/event emission are entirely up to the attacker, all of the "invariant checks" in this function (`ErrBalanceInvariance`) can be satisfied without any real value ever entering the module's escrow.

### Impact Explanation
An unprivileged attacker can:
1. Deploy a malicious ERC20 contract whose `transfer` function does nothing to real balances but emits a `Transfer` event (or returns `true`), and whose `balanceOf` returns attacker-chosen values.
2. Call `RegisterERC20` (permissionless path) to create a token pair for this contract.
3. Call `ConvertERC20` repeatedly to mint arbitrary amounts of a real native Cosmos coin backed by nothing.

This is unauthorized minting of spendable native value with no backing asset — a Critical accounting-corruption impact matching the "unauthorized minting... irreversible accounting corruption of spendable user value" criterion. The minted coins can then be freely transferred, used in other DeFi/precompile flows, or converted via IBC, propagating unbacked value throughout the chain.

### Likelihood Explanation
This requires only an unprivileged user to deploy a Solidity contract and submit two straightforward transactions (`RegisterERC20`, `ConvertERC20`); it does not require validator/relayer/governance privileges. The likelihood is high wherever `PermissionlessRegistration` is enabled (this is a governance-toggle exposed via `Params.permissionless_registration`, and tests confirm both permissioned and permissionless registration paths are supported and exercised, e.g. `tests/integration/x/erc20/test_proposals.go`). Whether it is a live, Critical, in-scope issue for a given deployment therefore depends on whether `PermissionlessRegistration` is enabled in that instance's params — this is a configuration-dependent precondition I could not verify against a live default value in this pass, so it is presented as a conditional Critical finding contingent on that parameter.

### Recommendation
- Do not use the arbitrary target contract's own `balanceOf`/event logs as the *sole* proof of value transfer for permissionlessly-registered native ERC20 pairs. Require deposit-and-verify patterns where the module's escrow balance is checked against a whitelisted/reference implementation, or restrict native-ERC20 registration+conversion for permissionless contracts until a stricter conformance check (e.g., bytecode/interface validation, a maximum initial supply cap, or a time-locked probation period) is enforced.
- Consider disabling `PermissionlessRegistration` by default and/or adding additional invariant checks that do not depend on values returned from the registered contract itself (e.g., cross-checking total supply changes, requiring a real escrow deposit prior to any minting).
- Long term, as recommended in the original report, avoid trusting inline "success" signals (return data, emitted logs, or self-reported balances) from arbitrary external code as the basis for critical accounting decisions; anchor invariants in state the protocol itself controls.

### Proof of Concept
1. Deploy `EvilERC20` with:
   ```solidity
   function transfer(address, uint256) external returns (bool) {
       emit Transfer(msg.sender, address(0xModule), 0); // spoof event, ignore amount arg from calldata via getters below
       return true;
   }
   function balanceOf(address) external view returns (uint256) {
       return storedFakeBalance; // attacker increments this at will, independent of actual token movement
   }
   ```
   (Exact wiring is trivial — `balanceOf` simply returns a counter the attacker bumps by `amount` on every fake `transfer` call, satisfying `expToken == balanceTokenAfter`.)
2. As any unprivileged account, submit `MsgRegisterERC20{Signer: attacker, Erc20Addresses: [EvilERC20]}` (works when `params.permissionless_registration == true`), per [1](#0-0) .
3. Submit `MsgConvertERC20{ContractAddress: EvilERC20, Amount: X, Receiver: attacker}`.
4. `convertERC20IntoCoinsForNativeToken` executes the fake `transfer`, passes `validateTransferEventExists` (spoofed log matches signature+address), passes the `balanceTokenAfter == expToken` check (attacker-controlled `balanceOf`), then calls `bankKeeper.MintCoins` and sends `X` native coins to the attacker — all per [2](#0-1) .
5. Repeat to mint unbounded amounts of the native coin denom, with no real ERC20 value ever transferred.

### Citations

**File:** x/erc20/keeper/msg_server.go (L92-130)
```go
	res, err := k.evmKeeper.CallEVMWithData(ctx, sender, &contract, transferData, true, nil)
	if err != nil {
		return nil, err
	}

	// Check evm call response
	var unpackedRet types.ERC20BoolResponse
	if len(res.Ret) == 0 {
		// if the token does not return a value, check for the transfer event in logs
		if err := validateTransferEventExists(res.Logs, contract); err != nil {
			return nil, err
		}
	} else {
		if err := erc20.UnpackIntoInterface(&unpackedRet, "transfer", res.Ret); err != nil {
			return nil, err
		}
		if !unpackedRet.Value {
			return nil, sdkerrors.Wrap(errortypes.ErrLogic, "failed to execute transfer")
		}
	}

	// Check expected escrow balance after transfer execution
	// NOTE: coin fields already validated in the ValidateBasic() of the message
	coins := sdk.Coins{sdk.Coin{Denom: pair.Denom, Amount: msg.Amount}}
	tokens := coins[0].Amount.BigInt()
	balanceTokenAfter := k.BalanceOf(ctx, erc20, contract, types.ModuleAddress)
	if balanceTokenAfter == nil {
		return nil, sdkerrors.Wrap(types.ErrEVMCall, "failed to retrieve balance")
	}

	expToken := big.NewInt(0).Add(balanceToken, tokens)

	if r := balanceTokenAfter.Cmp(expToken); r != 0 {
		return nil, sdkerrors.Wrapf(
			types.ErrBalanceInvariance,
			"invalid token balance - expected: %v, actual: %v",
			expToken, balanceTokenAfter,
		)
	}
```

**File:** x/erc20/keeper/msg_server.go (L324-350)
```go
// RegisterERC20 implements the gRPC MsgServer interface. Any account can permissionlessly
// register a native ERC20 contract to map to a Cosmos Coin.
func (k *Keeper) RegisterERC20(goCtx context.Context, req *types.MsgRegisterERC20) (*types.MsgRegisterERC20Response, error) {
	ctx := sdk.UnwrapSDKContext(goCtx)

	params := k.GetParams(ctx)

	if !params.PermissionlessRegistration {
		if err := k.validateAuthority(req.Signer); err != nil {
			return nil, err
		}
	}

	// Check if the conversion is globally enabled
	if !k.IsERC20Enabled(ctx) {
		return nil, types.ErrERC20Disabled.Wrap("registration is currently disabled by governance")
	}

	for _, addr := range req.Erc20Addresses {
		if !common.IsHexAddress(addr) {
			return nil, errortypes.ErrInvalidAddress.Wrapf("invalid ERC20 contract address: %s", addr)
		}

		pair, err := k.registerERC20(ctx, common.HexToAddress(addr))
		if err != nil {
			return nil, err
		}
```

**File:** x/erc20/keeper/util.go (L26-58)
```go
// validateTransferEventExists returns an error if the given transactions logs DO NOT include
// an expected `Transfer` event from the expected address
func validateTransferEventExists(logs []*types.Log, tokenAddress common.Address) error {
	if len(logs) == 0 {
		return errors.Wrapf(
			types2.ErrExpectedEvent, "expected Transfer event",
		)
	}
	found := false
	for _, log := range logs {
		if log.Topics[0] == logTransferSigHash.Hex() {
			if log.Address != tokenAddress.Hex() {
				return errors.Wrapf(
					types2.ErrUnexpectedEvent, "Transfer event from unexpected address",
				)
			}
			if found {
				return errors.Wrapf(
					types2.ErrUnexpectedEvent, "duplicate Transfer event",
				)
			}
			found = true
		}
	}

	if !found {
		return errors.Wrapf(
			types2.ErrExpectedEvent, "expected Transfer event",
		)
	}

	return nil
}
```
