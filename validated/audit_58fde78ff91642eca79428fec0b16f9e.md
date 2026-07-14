Let me check the `get_hashes_indexes` function and how `load_previous_hashes` is called in context.

### Title
`load_previous_hashes` Accepts Dirty-Leaf Blobs via `ParentFirstIterator`, Polluting `previous_hashes` with Uncommitted Hashes — (`crates/chia-datalayer/src/merkle/deltas.rs`)

### Summary

`DeltaFileCache::load_previous_hashes` decodes a zstd blob and iterates it with `ParentFirstIterator`, which performs **no dirty-flag check**. An attacker who supplies a crafted blob containing a leaf with `metadata.dirty = true` will have that leaf's hash silently inserted into `previous_hashes`. `seen_previous_hash` then returns `true` for a hash that was never part of any committed tree state.

### Finding Description

Two iterators exist with asymmetric dirty-flag enforcement:

**`LeftChildFirstIterator`** — used by `BlockStatusCache::new` → `MerkleBlob::new`: [1](#0-0) 

A dirty leaf causes `Error::DirtyLeaf` to propagate, so `MerkleBlob::new` (and `MerkleBlob::from_path`) reject any blob containing a dirty leaf. [2](#0-1) 

**`ParentFirstIterator`** — used by `load_previous_hashes`: [3](#0-2) 

`ParentFirstIterator` never inspects `block.metadata.dirty`. It yields every node unconditionally.

`load_previous_hashes` calls `zstd_decode_path` directly (bypassing `MerkleBlob::new`) and then iterates with `ParentFirstIterator`: [4](#0-3) 

Every node's hash — including dirty leaves — is inserted into `self.previous_hashes`. The query surface is: [5](#0-4) 

### Impact Explanation

`previous_hashes` is the set of hashes the DataLayer node considers to have existed in the prior committed tree state. Polluting it with dirty (uncommitted) hashes breaks the invariant that only committed state is "previously seen." Downstream logic that calls `seen_previous_hash` to gate state-transition acceptance or delta validation will treat forged uncommitted hashes as legitimate prior state, enabling acceptance of invalid DataLayer proofs or root transitions.

This matches: **High — DataLayer Merkle proof/blob/delta logic accepts forged inclusion/exclusion, corrupts tree roots, or lets untrusted input prove invalid state.**

### Likelihood Explanation

DataLayer nodes download blob files from peers as part of normal sync. The `load_previous_hashes` path takes an arbitrary `PathBuf`; any blob file sourced from an untrusted peer and passed to this function is a valid attack vector. No special privilege is required — only the ability to serve a crafted zstd-compressed blob file. The dirty flag is a single bit in the two-byte metadata prefix of any leaf block, trivially settable.

### Recommendation

Replace the raw `ParentFirstIterator` loop in `load_previous_hashes` with `MerkleBlob::from_path`, which already enforces dirty-leaf rejection via `LeftChildFirstIterator` inside `BlockStatusCache::new`. Alternatively, add an explicit dirty-flag check inside the loop:

```rust
pub fn load_previous_hashes(&mut self, path: &PathBuf) -> Result<(), Error> {
    let blob = crate::zstd_decode_path(path)?;
    self.previous_hashes = HashSet::new();
    if !blob.is_empty() {
        for item in ParentFirstIterator::new(&blob, None) {
            let (_, block) = item?;
            if block.metadata.dirty {
                return Err(Error::DirtyLeaf(/* index */));
            }
            self.previous_hashes.insert(block.node.hash());
        }
    }
    Ok(())
}
```

The cleanest fix is to route through `MerkleBlob::from_path` so the same validation path is used everywhere.

### Proof of Concept

1. Build a valid single-leaf blob (leaf at index 0, `dirty=false`).
2. Flip byte 1 of the block (the `dirty` field in `NodeMetadata`) to `0x01`.
3. zstd-compress the result and write it to a file.
4. Call `delta_file_cache.load_previous_hashes(&crafted_path)` — it returns `Ok(())`.
5. Call `delta_file_cache.seen_previous_hash(dirty_leaf_hash)` — returns `true`.
6. The same blob passed to `MerkleBlob::from_path` returns `Err(DirtyLeaf(...))`, confirming the asymmetry.

### Citations

**File:** crates/chia-datalayer/src/merkle/iterators.rs (L102-107)
```rust
            match block.node {
                Node::Leaf(..) => {
                    if block.metadata.dirty {
                        return Some(Err(Error::DirtyLeaf(item.index)));
                    }
                    return Some(Ok((item.index, block)));
```

**File:** crates/chia-datalayer/src/merkle/iterators.rs (L166-189)
```rust
impl Iterator for ParentFirstIterator<'_> {
    type Item = Result<(TreeIndex, Block), Error>;

    fn next(&mut self) -> Option<Self::Item> {
        // left sibling first, parents before children

        let index = self.deque.pop_front()?;
        let block = match try_get_block(self.blob, index) {
            Ok(block) => block,
            Err(e) => return Some(Err(e)),
        };

        if let Node::Internal(ref node) = block.node {
            if self.already_queued.contains(&index) {
                return Some(Err(Error::CycleFound()));
            }
            self.already_queued.insert(index);

            self.deque.push_back(node.left);
            self.deque.push_back(node.right);
        }

        Some(Ok((index, block)))
    }
```

**File:** crates/chia-datalayer/src/merkle/blob.rs (L316-332)
```rust
    pub fn new(blob: Vec<u8>) -> Result<Self, Error> {
        let length = blob.len();
        let remainder = length % BLOCK_SIZE;
        if remainder != 0 {
            return Err(Error::InvalidBlobLength(remainder));
        }

        let block_status_cache = BlockStatusCache::new(&blob)?;

        let self_ = Self {
            blob,
            block_status_cache,
            check_integrity_on_drop: cfg!(test),
        };

        Ok(self_)
    }
```

**File:** crates/chia-datalayer/src/merkle/deltas.rs (L35-46)
```rust
    pub fn load_previous_hashes(&mut self, path: &PathBuf) -> Result<(), Error> {
        let blob = crate::zstd_decode_path(path)?;
        self.previous_hashes = HashSet::new();

        if !blob.is_empty() {
            for item in ParentFirstIterator::new(&blob, None) {
                let (_, block) = item?;
                self.previous_hashes.insert(block.node.hash());
            }
        }
        Ok(())
    }
```

**File:** crates/chia-datalayer/src/merkle/deltas.rs (L56-58)
```rust
    pub fn seen_previous_hash(&self, hash: Hash) -> bool {
        self.previous_hashes.contains(&hash)
    }
```
