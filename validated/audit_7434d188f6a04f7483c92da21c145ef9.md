Based on the investigation, I found a concrete recursive-parsing analog reachable via untrusted proof data in the VC (Verified Credential) flow.

### Title
Unbounded recursion in `VCProofs.from_program`/`prove_keys` allows stack-exhaustion DoS from a crafted VC proof tree - (File: `chia/wallet/vc_wallet/vc_store.py`)

### Summary
The CVE describes a YAML parser (`Scanner::EnsureTokensInQueue`) that recurses without a depth bound on attacker-controlled input, exhausting the stack and crashing the process. `chia/wallet/vc_wallet/vc_store.py` contains a directly analogous pattern: `VCProofs.from_program()` and `VCProofs.prove_keys()` recursively walk an arbitrary CLVM `Program` tree with no depth limit, and this tree is built directly from bytes that reach the wallet via chain data / counterparty-supplied proofs tied to Verified Credentials.

### Finding Description
`VCProofs.from_program()` recursively descends into `prog.at("f")`/`prog.at("r")` for every non-atom pair, with no maximum-depth guard: [1](#0-0) 

`prove_keys()` performs the same unbounded pairwise recursion over `tree.first()`/`tree.rest()`: [2](#0-1) 

`VCStore.get_proofs_for_root()` feeds `Program.from_bytes()` output (an attacker/issuer-controlled proof blob) directly into `VCProofs.from_program()`: [3](#0-2) 

Unlike the deliberately-iterative `sha256_treehash()` helper used elsewhere in the codebase specifically to avoid "blowing out the python stack" on deeply nested CLVM trees: [4](#0-3) 

`VCProofs.from_program`/`prove_keys` use plain Python recursion instead, so a proof program built as a deeply right- or left-nested cons chain (e.g., thousands of levels) will hit Python's recursion limit or exhaust the interpreter stack before any size/complexity check is applied.

### Impact Explanation
A VC proof provider (or any party able to hand a wallet a crafted VC proof blob, e.g. through the proof-provider workflow or a stored/synced VC coin) can construct a proof `Program` with pathological nesting depth. When the wallet later calls `VCProofs.from_program()` (via `get_proofs_for_root()`) or `prove_keys()` to inspect/redact proofs, the recursive walk can crash the wallet process (`RecursionError` / native stack overflow), a denial-of-service against the wallet consistent with the CVE's "stack consumption and application crash" characterization. This is scoped to the VC/CR-CAT flow explicitly called out as in-scope.

### Likelihood Explanation
Likelihood is constrained by reach: this path is triggered by wallet-side processing of VC proof data (proof-provider issued proofs, stored via `add_vc_proofs`/`get_proofs_for_root`), not by a mempool-wide unauthenticated spend bundle. It requires a malicious or compromised VC/proof-provider counterparty to supply a pathologically nested proof tree to a wallet that uses the VC feature, which narrows the exposed population but is fully attacker-triggerable without any privileged access once that interaction occurs.

### Recommendation
Rewrite `VCProofs.from_program()` and `prove_keys()` as iterative (stack-based) tree walks, mirroring the pattern already used in `sha256_treehash()`, and/or enforce an explicit maximum-depth check before recursing, rejecting proof programs beyond a sane bound.

### Proof of Concept
1. Construct a deeply nested CLVM cons-pair program representing a "proofs" tree with e.g. 50,000+ nested pair levels (each level having a non-atom `first`/`rest` so the `first.atom is None and rest.atom is None` branch of `from_program` keeps recursing).
2. Serialize it and have it delivered as a VC proof blob to a wallet's `vc_proofs` table (e.g., via the normal VC proof-provider/proof-sync path that ultimately calls `VCStore.get_proofs_for_root` → `VCProofs.from_program`).
3. Observe the wallet process crash with a `RecursionError` or a native stack overflow when the recursive parser exceeds the interpreter's call-stack limits, matching the "stack consumption and application crash" behavior described in CVE-2018-20573.

### Citations

**File:** chia/wallet/vc_wallet/vc_store.py (L43-55)
```python
    @staticmethod
    def from_program(prog: Program) -> VCProofs:
        first: Program = prog.at("f")
        rest: Program = prog.at("r")
        if first.atom is None and rest.atom is None:
            final_dict: dict[str, str] = {}
            final_dict.update(VCProofs.from_program(first).key_value_pairs)
            final_dict.update(VCProofs.from_program(rest).key_value_pairs)
            return VCProofs(final_dict)
        elif first.atom is not None and rest.atom is not None:
            return VCProofs({first.atom.decode("utf-8"): rest.atom.decode("utf-8")})
        else:
            raise ValueError("Malformatted VCProofs program")  # pragma: no cover
```

**File:** chia/wallet/vc_wallet/vc_store.py (L57-81)
```python
    def prove_keys(self, keys: list[str], tree: Program | None = None) -> Program:
        if tree is None:
            tree = self.as_program()

        first = tree.first()
        if first.atom is not None:
            if first.atom.decode("utf8") in keys:
                return tree
            else:
                tree_hash_as_atom: Program = Program.to(tree.get_tree_hash())
                return tree_hash_as_atom
        else:
            rest = tree.rest()
            first_tree = self.prove_keys(keys, first)
            rest_tree = self.prove_keys(keys, rest)
            if first_tree.atom is not None and rest_tree.atom is not None:
                tree_hash_as_atom = Program.to(
                    Program.to((first_tree, rest_tree)).get_tree_hash_precalc(
                        bytes32(first_tree.atom), bytes32(rest_tree.atom)
                    )
                )
                return tree_hash_as_atom
            else:
                new_tree: Program = first_tree.cons(rest_tree)
                return new_tree
```

**File:** chia/wallet/vc_wallet/vc_store.py (L252-260)
```python
    async def get_proofs_for_root(self, root: bytes32) -> VCProofs | None:
        async with self.db_wrapper.reader_no_transaction() as conn:
            cursor = await conn.execute("SELECT proofs FROM vc_proofs WHERE root=?", (root.hex(),))
            row = await cursor.fetchone()
            await cursor.close()
            if row is None:
                return None  # pragma: no cover
            else:
                return VCProofs.from_program(Program.from_bytes(row[0]))
```

**File:** chia/types/blockchain_format/tree_hash.py (L1-7)
```python
"""
This is an implementation of `sha256_treehash`, used to calculate
puzzle hashes in clvm.

This implementation goes to great pains to be non-recursive so we don't
have to worry about blowing out the python stack.
"""
```
