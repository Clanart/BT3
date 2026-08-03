[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L126-133)
```text
    struct MultisigAccount has key {
        // The list of all owner addresses.
        owners: vector<address>,
        // The number of signatures required to pass a transaction (k in k-of-n).
        num_signatures_required: u64,
        // Map from transaction id (incrementing id) to transactions to execute for this multisig account.
        // Already executed transactions are deleted to save on storage but can always be accessed via events.
        transactions: Table<u64, MultisigTransaction>,
```

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L180-181)
```text
    /// A transaction to be executed in a multisig account.
    /// This must contain either the full transaction payload or its hash (stored as bytes).
```

**File:** aptos-move/framework/aptos-framework/sources/multisig_account.move (L182-191)
```text
    struct MultisigTransaction has copy, drop, store {
        payload: Option<vector<u8>>,
        payload_hash: Option<vector<u8>>,
        // Mapping from owner adress to vote (yes for approve, no for reject). Uses a simple map to deduplicate.
        votes: SimpleMap<address, bool>,
        // The owner who created this transaction.
        creator: address,
        // The timestamp in seconds when the transaction was created.
        creation_time_secs: u64,
    }
```
