### Title
Missing zero-address validation in ERC20 precompile `transfer`/`transferFrom` permits permanent, irrecoverable loss of user funds - (File: precompiles/erc20/tx.go)

### Summary
The `ParseTransferArgs`/`ParseTransferFromArgs` helpers and the shared `transfer` handler in the native ERC20 precompile never validate that the destination address is non-zero, unlike standard ERC20 implementations bundled elsewhere in the repo (which explicitly `require(to != address(0))`). This mirrors the 1inch `LimitOrderProtocol` zero-address bug class, but here the consequence is a Critical loss of user-controlled bank-backed value rather than a mere call-target sanity check.

### Finding Description
`ParseTransferArgs` and `ParseTransferFromArgs` decode the destination address purely by ABI type-assertion, with no zero-address check: [1](#0-0) [2](#0-1) 

Both `Transfer` and `TransferFrom` forward `to` unchecked into the shared `transfer` function, which builds a `banktypes.MsgSend` and executes it via the bank keeper: [3](#0-2) 

`MsgSend`'s only safety net is the bank module's `BlockedAddr` list, which by convention contains module accounts, not the all-zero address. Consequently `to.Bytes()` (twenty zero bytes) is accepted as a normal, valid, unblocked `sdk.AccAddress`, and the coins are moved there successfully. No private key can ever control the all-zero address, so funds sent there are permanently unspendable — yet `BankKeeper.GetSupply` and the token pair's ERC20 view still count them as circulating, corrupting the assumed 1:1 mapping between "circulating" and "recoverable" spendable value.

Critically, this is reachable through `TransferFrom`, where the caller (`spenderAddr`) is not the token owner (`from`) but merely holds an allowance: [4](#0-3) 

Any address holding an allowance over a victim's tokens (e.g., a router/DeFi contract address the victim approved for a swap, or any other legitimately-granted allowance) can call `transferFrom(victim, address(0), amount)` and permanently destroy up to the full allowance amount of the victim's balance, with the loss disguised as an ordinary successful transfer (a `Transfer` event to `address(0)` is even emitted, which off-chain tooling would normally interpret as a burn — but no `BurnCoins` call occurs, so total supply accounting is not decremented, compounding the corruption).

### Impact Explanation
This satisfies the Critical bar for "permanent freezing, locking, theft, or unauthorized extraction of user funds ... or token-pair-backed balances": any unprivileged actor holding an allowance can irrecoverably destroy another user's tokens without their consent, and the destroyed value remains phantom-counted in total supply, corrupting protocol-wide accounting invariants between the bank module's `GetSupply` and actually recoverable balances.

### Likelihood Explanation
Likelihood is high: allowances are a normal, expected part of ERC20 usage (approvals to DEXes, lending protocols, etc.), and the attack requires only a single unprivileged `transferFrom` call with `to = address(0)` — no special privileges, timing, or race conditions are needed.

### Recommendation
Add explicit zero-address checks in `ParseTransferArgs` and `ParseTransferFromArgs` (and symmetrically for `from`), rejecting `to == common.Address{}` (and `from == common.Address{}`) before constructing the `MsgSend`, consistent with the `_transfer`/`_mint`/`_burn` zero-address guards already present in the bundled Solidity ERC20 reference implementations: [5](#0-4) 

### Proof of Concept
1. Victim approves a router/dApp contract (or any address) for an allowance `A` on the native ERC20 precompile for token pair `T` via `approve(spender, A)`.
2. The spender (attacker-controlled or attacker exploiting a compromised/malicious integration) calls `T.transferFrom(victim, address(0), A)`.
3. `ParseTransferFromArgs` accepts `to = 0x000...000` without error; `p.transfer` executes `MsgSend(victim, 0x000...000, A)` via the bank keeper, which succeeds because the zero address is not in `BlockedAddr`.
4. The victim's balance decreases by `A` permanently; no account can ever access those funds; `GetSupply` for the denom is unchanged, so total supply no longer matches recoverable balances.

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

**File:** precompiles/erc20/testdata/ERC20NoMetadata.sol (L211-213)
```text
    ) internal virtual {
        require(from != address(0), "ERC20: transfer from the zero address");
        require(to != address(0), "ERC20: transfer to the zero address");
```
