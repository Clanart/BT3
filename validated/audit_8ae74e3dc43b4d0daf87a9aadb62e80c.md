### Title
Bank Precompile `send` Trusts Caller-Supplied `from` Address Without Verifying True Fund Owner - (File: `precompiles/bank/bank.go`)

### Summary
The `IBank.send(from, to, denom, amount)` transaction handler in the bank precompile authorizes a fund transfer solely by checking that `msg.sender` (the EVM `caller`) equals the registered ERC20-native pointer contract address for `denom`. It never checks that the `from` argument corresponds to the entity that actually initiated the underlying EVM call (i.e., the pointer contract's own `msg.sender`). The Sei/Cosmos account whose balance is debited is taken directly, and un-validated, from the caller-supplied `args[0]` parameter.

### Finding Description
In `precompiles/bank/bank.go`, `send()` performs authorization purely at the "which contract may call this precompile" level: [1](#0-0) 

Once that single check passes, the actual debited/credited Sei addresses are derived straight from the caller-supplied arguments with no further ownership or signature check: [2](#0-1) 

This is structurally the same bug class as the Rallly IDOR: an identifier that is supposed to represent "the acting user" (`participantId` in Rallly, `from`/`senderSeiAddr` here) is accepted as a raw caller-supplied parameter and used to mutate that identified party's state, with authorization checked against the wrong entity (the calling contract's identity) instead of against the party named in the parameter. The precompile itself provides zero defense-in-depth: it delegates 100% of "does `from` actually authorize this transfer" enforcement to whichever contract happens to be registered as the ERC20 native pointer for that denom. Every legacy version of this precompile (`v552` through `v67` and the current `bank.go`) has the identical pattern, so this authorization gap has existed unchanged across the precompile's entire history. [3](#0-2) 

### Impact Explanation
If the registered ERC20-native pointer contract for any denom ever fails to strictly enforce `from == msg.sender` (or a valid ERC20 allowance from `from` to `msg.sender`) before forwarding to `IBank.send`, any EVM caller could move usei/native-token balances out of an arbitrary Sei account by simply supplying that account as `args[0]`. Because the precompile's own guard only checks "is the caller the pointer contract," any such pointer-side omission — whether from a future upgrade, a non-standard/community-deployed pointer implementation, or a bug in the pointer bytecode — translates directly into unauthorized transfer of funds from arbitrary users, i.e. concrete fund loss, exactly the class of impact this exercise requires. The precompile provides no secondary check to catch it.

### Likelihood Explanation
Exploitability depends entirely on the pointer contract enforcing `from == msg.sender`/allowance semantics correctly on every code path (`transfer`, `transferFrom`, and any future entry points). I was not able to locate and fully audit the actual deployed Solidity/bytecode source for the native ERC20 pointer contracts within the indexed portion of this repository (only generic example ERC20 contracts under `evmrpc/solidity` and `example/contracts` were found, not the genesis/pointer implementation itself), so I cannot confirm whether such a bypass currently exists in the shipped pointer bytecode. This is a real architectural weak point (single point of trust with no defense-in-depth in the precompile), but without confirming an actual bypass in the pointer contract's own authorization logic, I cannot assert a currently-exploitable end-to-end path with full confidence.

### Recommendation
Add a redundant ownership/authorization check inside the precompile itself rather than relying solely on the identity of the calling contract — e.g., verify that `senderSeiAddr` corresponds to an address that the pointer contract is entitled to move funds for (such as by requiring the pointer to also pass through the true EOA/tx-origin, or by having the precompile independently check an allowance record), so that a defect in any single pointer implementation cannot result in unauthorized transfers.

### Proof of Concept
Not fully constructible from the indexed code: exploitation requires the concrete bytecode/logic of the registered ERC20 native pointer contract (to show it does not itself check `from == msg.sender`), which was not found in the available index. Given access to a Devin session with full repo/filesystem access, the next step would be to locate the genesis-deployed pointer bytecode/source (e.g. via `x/evm` genesis or `NativeSeiTokenERC20`-style contracts) and confirm whether `transfer`/`transferFrom` forward `from` unchecked to `IBank.send`.

### Citations

**File:** precompiles/bank/bank.go (L209-216)
```go
	denom := args[2].(string)
	if denom == "" {
		return nil, 0, errors.New("invalid denom")
	}
	pointer, _, exists := p.evmKeeper.GetERC20NativePointer(ctx, denom)
	if !exists || pointer.Cmp(caller) != 0 {
		return nil, 0, fmt.Errorf("only pointer %s can send %s but got %s", pointer.Hex(), denom, caller.Hex())
	}
```

**File:** precompiles/bank/bank.go (L223-245)
```go
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

**File:** precompiles/bank/legacy/v67/bank.go (L215-232)
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
```
