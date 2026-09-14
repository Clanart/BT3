### Title
Unhandled `IndexError` on empty/short memo list crashes Data Layer mirror-coin parsing - ([File: chia/wallet/db_wallet/db_wallet_puzzles.py])

### Summary
`get_mirror_info()` parses the CLVM output of a `CREATE_COIN` condition whose puzzle hash matches the Data Layer "mirror" puzzle and unconditionally indexes into the resulting memo list (`memos[0]`) without first checking that the list is non-empty. Because the memo list originates from a puzzle/solution pair that is fully attacker-controlled (any coin spend can create a coin with the mirror puzzle hash and an arbitrary, even empty, memo list), a crafted spend can trigger an out-of-bounds list access and an unhandled `IndexError`, analogous to the out-of-bounds array access described in CVE-2016-9433 (crafted input parsed without a bounds check causing a crash).

### Finding Description
`get_mirror_info()` runs the parent puzzle/solution to obtain the resulting conditions, finds the `CREATE_COIN` condition whose destination puzzle hash equals the mirror puzzle hash, and then reads the memo list from that condition: [1](#0-0) 

```
memos: list[bytes] = condition.at("rrrf").as_python()
launcher_id = bytes32(memos[0])
return launcher_id, [url for url in memos[1:]]
```

The memo list (`condition.at("rrrf")`) is the third argument of a `CREATE_COIN` condition, which is fully controlled by the puzzle/solution that produced it — i.e., by whoever created the mirror coin. There is no length check before `memos[0]` is accessed. If a mirror coin is created with `CREATE_COIN <mirror_ph> <amount> ()` (an empty list) or any memo structure that, once converted `as_python()`, yields a list shorter than 1 element, `memos[0]` raises `IndexError: list index out of range`, an unhandled Python exception.

This function is invoked from the Data Layer wallet code (`chia/data_layer/data_layer_wallet.py`) when a wallet processes DL mirror coin state — a path reachable purely by observing/syncing chain data created by any Data Layer participant, not requiring any privileged access. Any Data Layer client wallet that encounters such a maliciously-crafted mirror coin on-chain will crash while parsing it, since the call site does not appear to wrap this in a broad exception handler for the "empty memo" case (only a `ValueError` is raised deliberately when no matching `CREATE_COIN` condition is found; the `IndexError` from empty memos is not converted to that same, presumably-handled, exception type).

### Impact Explanation
This is a spend-triggered denial-of-service: a single, cheaply-constructed coin spend (creating a coin at the DL mirror puzzle hash with an empty or too-short memo list) can be confirmed on-chain by anyone, and any wallet client running the Data Layer feature that syncs/parses that coin will hit an unhandled `IndexError`. Depending on how the caller in `data_layer_wallet.py` handles exceptions, this can halt Data Layer wallet-state processing for affected wallets, matching the "spend-triggered transaction-processing halt" acceptance criterion.

### Likelihood Explanation
Likelihood is high for any Data Layer client that ends up syncing a coin created this way: constructing the malicious `CREATE_COIN` condition requires only a standard spend with a solution producing `(CREATE_COIN <mirror_puzzle_hash> <amount> ())` (or any degenerate memo list), which is trivial and available to any unprivileged coin owner able to submit a spend bundle.

### Recommendation
Add a length check on `memos` before indexing (`if not memos or len(memos) == 0: raise ValueError(...)`), and ensure the exception path in `data_layer_wallet.py` treats malformed mirror coins the same way it treats "not a mirror coin" — i.e., convert to the deliberate `ValueError` (or another handled exception) rather than allowing an `IndexError` to propagate.

### Proof of Concept
1. Create a coin spend whose puzzle reveal/solution, when run, outputs a condition list containing `(CREATE_COIN <mirror_puzzle_hash> <amount> ())` — i.e., the `CREATE_COIN` condition's memo field is an empty list — targeting the well-known mirror puzzle hash (`create_mirror_puzzle().get_tree_hash()`), as computed in `chia/wallet/db_wallet/db_wallet_puzzles.py`. [2](#0-1) 
2. Submit and confirm this spend on-chain (any unprivileged wallet can do this — no special permission is required to create a coin at an arbitrary puzzle hash).
3. Any Data Layer client wallet that subsequently processes/parses this coin via `get_mirror_info()` (through `chia/data_layer/data_layer_wallet.py`) will raise an unhandled `IndexError` at `memos[0]`, crashing or breaking Data Layer wallet-state processing for that coin.

Note: I was unable to fully trace the exact exception-handling behavior at every call site in `chia/data_layer/data_layer_wallet.py` (whether some call sites already wrap this in a broad `try/except` that would downgrade this to a lower-impact bug) due to running out of tool iterations; this should be verified by reading the full call sites in that file before finalizing severity.

### Citations

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L90-95)
```python
def create_mirror_puzzle() -> Program:
    return P2_PARENT.curry(Program.to(1))


MIRROR_PUZZLE_HASH = create_mirror_puzzle().get_tree_hash()

```

**File:** chia/wallet/db_wallet/db_wallet_puzzles.py (L97-110)
```python
def get_mirror_info(
    parent_puzzle: Program | SerializedProgram, parent_solution: Program | SerializedProgram
) -> tuple[bytes32, list[bytes]]:
    assert type(parent_puzzle) is type(parent_solution)
    _, conditions = run_with_cost(parent_puzzle, INFINITE_COST, parent_solution)
    for condition in conditions.as_iter():
        if (
            condition.first().as_python() == ConditionOpcode.CREATE_COIN
            and condition.at("rf").as_python() == create_mirror_puzzle().get_tree_hash()
        ):
            memos: list[bytes] = condition.at("rrrf").as_python()
            launcher_id = bytes32(memos[0])
            return launcher_id, [url for url in memos[1:]]
    raise ValueError("The provided puzzle and solution do not create a mirror coin")
```
