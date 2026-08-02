[1](#0-0) [2](#0-1)

### Citations

**File:** aptos-move/aptos-vm-types/src/abstract_write_op.rs (L175-199)
```rust
/// Describes an update to a resource group granularly, with WriteOps to affected
/// member resources of the group, as well as a separate WriteOp for metadata and size.
#[derive(PartialEq, Eq, Clone, Debug)]
pub struct GroupWrite {
    /// Op of the correct kind (creation / modification / deletion) and metadata, and
    /// the size of the group after the updates encoded in the bytes (no bytes for
    /// deletion). Relevant during block execution, where the information read to
    /// derive metadata_op will be validated during parallel execution to make sure
    /// it is correct, and the bytes will be replaced after the transaction is committed
    /// with correct serialized group update to obtain storage WriteOp.
    pub metadata_op: WriteOp,
    /// Updates to individual group members. WriteOps are 'legacy', i.e. no metadata.
    /// If the metadata_op is a deletion, all (correct) inner_ops should be deletions,
    /// and if metadata_op is a creation, then there may not be a creation inner op.
    /// Not vice versa, e.g. for deleted inner ops, other untouched resources may still
    /// exist in the group. Note: During parallel block execution, due to speculative
    /// reads, this invariant may be violated (and lead to speculation error if observed)
    /// but guaranteed to fail validation and lead to correct re-execution in that case.
    pub(crate) inner_ops: BTreeMap<StructTag, (WriteOp, Option<TriompheArc<MoveTypeLayout>>)>,
    /// Group size as used for gas charging, None if (metadata_)op is Deletion.
    pub(crate) maybe_group_op_size: Option<ResourceGroupSize>,
    // TODO: consider Option<u64> to be able to represent a previously non-existent group,
    //       if useful
    pub(crate) prev_group_size: u64,
}
```

**File:** aptos-move/aptos-vm-types/src/abstract_write_op.rs (L254-278)
```rust
#[derive(PartialEq, Eq, Clone, Debug)]
/// Note that write_op can be a Deletion, as long as the Move type layout contains
/// a delayed field. This simplifies squashing session outputs, in particular.
pub struct WriteWithDelayedFieldsOp {
    pub write_op: WriteOp,
    pub layout: TriompheArc<MoveTypeLayout>,
    pub materialized_size: Option<u64>,
}

/// Actual information on which delayed fields were read is unnecessary
/// in the current implementation, as we need to materialize the whole value anyways.
///
/// If future implementation needs those - they can be added.
#[derive(PartialEq, Eq, Clone, Debug)]
pub struct InPlaceDelayedFieldChangeOp {
    pub layout: TriompheArc<MoveTypeLayout>,
    pub materialized_size: u64,
    pub metadata: StateValueMetadata,
    /// Set to true only for aggregator V1 deltas. The flag enforces the legacy
    /// behavior:
    ///   - legacy (none) metadata,
    ///   - delta is excluded from storage-fee charging.
    /// False for all other in-place changes.
    pub is_aggregator_v1_delta: bool,
}
```
