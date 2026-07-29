[1](#0-0) [2](#0-1)

### Citations

**File:** x/vm/keeper/abci.go (L35-36)
```go
	k.SetHeaderHash(ctx)
	return nil
```

**File:** x/vm/types/preinstall.go (L34-38)
```go
	{
		Name:    "EIP-2935 - Serve historical block hashes from state",
		Address: params.HistoryStorageAddress.String(),
		Code:    common.Bytes2Hex(params.HistoryStorageCode),
	},
```
