Confirmed: `ParseTransferArgs`/`ParseTransferFromArgs` never reject `to == common.Address{}` (the zero address), and the `transfer` helper builds a `banktypes.MsgSend` straight from that value. This is the concrete Cosmos EVM analog of the report's missing `to != address(0)` check.

### Title
Unrestricted transfers to the zero address via the ERC20 precompile permanently lock user funds - (File: `precompiles/erc20/tx.go`, `precompiles/erc20/types.go`)

### Summary
The `GigaNameNFTBeforeUpdateHandler.update()` bug is a missing `to != address(0)` guard before mutating state for a recipient. The same missing-zero-address-check pattern exists in the ERC-20 precompile's transfer path: `ParseTransferArgs`/`ParseTransferFromArgs` accept `to == address(0)` and `transfer()` forwards it unchecked into a `bank.MsgSend`.

### Finding Description
`ParseTransferArgs` [1](#0-0)  and `ParseTransferFromArgs` [2](#0-1)  validate only the type of the `to` argument (`common.Address`), never that it is non-zero. `Precompile.transfer` then builds and dispatches a bank `MsgSend` directly from that value: [3](#0-2) .

The forked `Send` message handler only rejects `BlockedAddr` recipients (module accounts and static/available precompile addresses configured in `BlockedAddresses()` [4](#0-3) ); it performs no check that the recipient is the canonical zero address. Standard Solidity ERC20 implementations bundled in this same repo explicitly guard against this (`require(to != address(0), "ERC20: transfer to the zero address")` [5](#0-4) ), but the native Go precompile implementation that mediates real bank-backed balances lacks the equivalent guard.

Because `common.Address{}` maps to a valid (though uncontrolled) 20-byte Cosmos `AccAddress`, and that address is not in `BlockedAddresses()`, any unprivileged caller can invoke `transfer(address(0), amount)` or `transferFrom(owner, address(0), amount)` (with sufficient allowance) through the ERC20 precompile. The bank `SendCoins` executes successfully, moving spendable coin balance to the zero address — an account with no known private key and not recognized by any burn/mint accounting path, so total supply is unaffected while the tokens become permanently unspendable and unrecoverable by anyone, including governance-driven remediation without a bespoke migration.

### Impact Explanation
This matches the in-scope "permanent freezing, locking… of user funds" impact category: any user (or automated integration, e.g., an approved spender via `transferFrom`) can cause irreversible loss of a token-pair-backed balance for themselves or an approved owner's funds, with no code path to recover them. Unlike the audited Solidity ERC20 reference implementations shipped in this repo (which explicitly revert on `to == address(0)`), the production precompile that actually moves real bank balances for registered token pairs has no such protection.

### Likelihood Explanation
High likelihood: the trigger is a single, ordinary EVM call to a standard precompile method (`transfer`/`transferFrom`) with a zero address argument — no privileged access, race condition, or unusual sequencing is required. It can happen accidentally (a bug in a caller contract/dApp constructing a token transfer to a default/zero address) or be induced maliciously against a victim who has granted an allowance.

### Recommendation
Add an explicit zero-address check in `ParseTransferArgs`/`ParseTransferFromArgs` (or in `Precompile.transfer` before constructing `banktypes.MsgSend`), rejecting `to == common.Address{}` with an ERC20-style error (`ERC20InvalidReceiver`), mirroring the guard already present in the bundled Solidity reference contracts.

### Proof of Concept
1. Deploy/attach to a registered ERC20 token-pair precompile address `P`.
2. Fund account `A` with the paired denom so `A` has spendable balance via the precompile.
3. From `A`, call `P.transfer(address(0), amount)`.
4. Observe `msgSrv.Send` succeeds (`to` is not in `BlockedAddresses()`), the `Transfer` event is emitted, and `A`'s balance decreases while the coins land on the zero-address `AccAddress` — unspendable and unrecoverable, with no compensating burn of total supply.

### Citations

**File:** precompiles/erc20/types.go (L26-44)
```go
func ParseTransferArgs(args []interface{}) (
	to common.Address, amount *big.Int, err error,
) {
	if len(args) != 2 {
		return common.Address{}, nil, fmt.Errorf("invalid number of arguments; expected 2; got: %d", len(args))
	}

	to, ok := args[0].(common.Address)
	if !ok {
		return common.Address{}, nil, fmt.Errorf("invalid to address: %v", args[0])
	}

	amount, ok = args[1].(*big.Int)
	if !ok {
		return common.Address{}, nil, fmt.Errorf("invalid amount: %v", args[1])
	}

	return to, amount, nil
}
```

**File:** precompiles/erc20/types.go (L48-71)
```go
func ParseTransferFromArgs(args []interface{}) (
	from, to common.Address, amount *big.Int, err error,
) {
	if len(args) != 3 {
		return common.Address{}, common.Address{}, nil, fmt.Errorf("invalid number of arguments; expected 3; got: %d", len(args))
	}

	from, ok := args[0].(common.Address)
	if !ok {
		return common.Address{}, common.Address{}, nil, fmt.Errorf("invalid from address: %v", args[0])
	}

	to, ok = args[1].(common.Address)
	if !ok {
		return common.Address{}, common.Address{}, nil, fmt.Errorf("invalid to address: %v", args[1])
	}

	amount, ok = args[2].(*big.Int)
	if !ok {
		return common.Address{}, common.Address{}, nil, fmt.Errorf("invalid amount: %v", args[2])
	}

	return from, to, amount, nil
}
```

**File:** precompiles/erc20/tx.go (L69-116)
```go
func (p *Precompile) transfer(
	ctx sdk.Context,
	contract *vm.Contract,
	stateDB vm.StateDB,
	method *abi.Method,
	from, to common.Address,
	amount *big.Int,
) (data []byte, err error) {
	coins := sdk.Coins{{Denom: p.tokenPair.Denom, Amount: math.NewIntFromBigInt(amount)}}

	msg := banktypes.NewMsgSend(from.Bytes(), to.Bytes(), coins)

	if err = msg.Amount.Validate(); err != nil {
		return nil, err
	}

	isTransferFrom := method.Name == TransferFromMethod
	spenderAddr := contract.Caller()
	newAllowance := big.NewInt(0)

	if isTransferFrom {
		prevAllowance, err := p.erc20Keeper.GetAllowance(ctx, p.Address(), from, spenderAddr)
		if err != nil {
			return nil, ConvertErrToERC20Error(err)
		}

		newAllowance = new(big.Int).Sub(prevAllowance, amount)
		if newAllowance.Sign() < 0 {
			return nil, ErrInsufficientAllowance
		}

		if newAllowance.Sign() == 0 {
			// If the new allowance is 0, we need to delete it from the store.
			err = p.erc20Keeper.DeleteAllowance(ctx, p.Address(), from, spenderAddr)
		} else {
			// If the new allowance is not 0, we need to set it in the store.
			err = p.erc20Keeper.SetAllowance(ctx, p.Address(), from, spenderAddr, newAllowance)
		}
		if err != nil {
			return nil, ConvertErrToERC20Error(err)
		}
	}

	msgSrv := NewMsgServerImpl(p.BankKeeper)
	if err = msgSrv.Send(ctx, msg); err != nil {
		// This should return an error to avoid the contract from being executed and an event being emitted
		return nil, ConvertErrToERC20Error(err)
	}
```

**File:** config/evmd_config.go (L52-82)
```go
// BlockedAddresses returns all the app's blocked account addresses.
//
// Note, this includes:
//   - module accounts
//   - Ethereum's native precompiled smart contracts
//   - Cosmos EVM' available static precompiled contracts
func BlockedAddresses() map[string]bool {
	blockedAddrs := make(map[string]bool)

	maccPerms := GetMaccPerms()
	accs := make([]string, 0, len(maccPerms))
	for acc := range maccPerms {
		accs = append(accs, acc)
	}
	sort.Strings(accs)

	for _, acc := range accs {
		blockedAddrs[authtypes.NewModuleAddress(acc).String()] = true
	}

	blockedPrecompilesHex := evmtypes.AvailableStaticPrecompiles
	for _, addr := range corevm.PrecompiledAddressesPrague {
		blockedPrecompilesHex = append(blockedPrecompilesHex, addr.Hex())
	}

	for _, precompile := range blockedPrecompilesHex {
		blockedAddrs[cosmosevmutils.Bech32StringFromHexAddress(precompile)] = true
	}

	return blockedAddrs
}
```

**File:** precompiles/erc20/testdata/ERC20NoMetadata.sol (L211-213)
```text
    ) internal virtual {
        require(from != address(0), "ERC20: transfer from the zero address");
        require(to != address(0), "ERC20: transfer to the zero address");
```
