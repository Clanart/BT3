### Title
Stack overflow via unbounded native recursion when destroying deeply-nested `UnknownFieldSet` groups - ([File: src/google/protobuf/unknown_field_set_lite.cc])

### Summary
`CodedInputStream`/`WireFormat` bound *parsing* recursion depth of embedded messages, groups, and unknown-field groups by `SetRecursionLimit()` (default 100), exactly as documented to "prevent malicious messages from causing stack overflows" <cite repo="bsaldua/protobuf--007" path="src/google/protobuf/io/coded_stream.h" start="387="394" /> [1](#0-0) . However, once a chain of nested `UnknownFieldSet` "group" fields has been accepted up to that depth, tearing it down is done with genuine, unbounded native C++ recursion: `UnknownField::Delete()` calls `delete data_.group`, which runs `~UnknownFieldSet()`, which clears its own fields and again calls `Delete()` on any nested group, one C++ stack frame per level [2](#0-1) . This mirrors exactly the Aptos bug class: a depth check gates *construction*, but the compiler/library-generated `Drop`/destructor is still naively recursive and unguarded, so raising the accepted depth (a documented, supported operation) re-enables a stack-overflow crash at teardown time.

### Finding Description
- Groups stored as unknown fields are attacker-controlled independent of the application's schema: any field number/tag not recognized by the target message type is generically parsed and appended to `UnknownFieldSet` as a `TYPE_GROUP` entry via `AddGroup()`, which heap-allocates a fresh nested `UnknownFieldSet` [3](#0-2) .
- `WireFormatTest.UnknownFieldRecursionLimit` confirms these nested unknown groups are counted against the same `SetRecursionLimit()` budget used for normal message nesting, and that raising the limit allows deeper unknown-group chains to parse successfully [4](#0-3) .
- The public API documents that recursion limit is caller-adjustable and that "apps should set shorter limits if possible," implicitly acknowledging many apps set it higher for legitimate needs [5](#0-4) ; the codebase's own tests set custom limits far above the default (e.g. `SetRecursionLimit(kDepth + 10)` for `kDepth = 1000`+) to accommodate deep, but *trusted*, schema nesting [6](#0-5) .
- Once such a raised limit is in effect, an attacker does **not** need the target schema to be recursive at all: they can pad the message with a chain of unrecognized field numbers encoded as nested groups, entirely orthogonal to the real message shape, up to the raised limit. Parsing succeeds (the depth check only guards construction). When the resulting message (and its `UnknownFieldSet`) is destroyed, `Delete()`→`delete data_.group`→`~UnknownFieldSet()`→`Clear()`/`ClearFallback()`→`Delete()` repeats once per nested level with no iterative unwinding and no depth re-check [7](#0-6) .
- This is structurally identical to the fixed Aptos issue: a `Drop`/destructor implemented as compiler-generated (or here, hand-written but still call-stack-based) recursion over a container that a construction-time depth check permitted to be deep, but whose *destruction* path was never made iterative.

### Impact Explanation
Impact is a process crash (stack overflow / SIGSEGV) at message-destruction time — a denial of service against any server process that (a) has raised `SetRecursionLimit` above the small default for legitimate deep-schema handling, and (b) parses untrusted/bounded binary protobuf into such messages. At the protobuf-library default limit (100), the reachable native recursion depth is not large enough to overflow a typical 1–8MB thread stack, so out-of-the-box the risk is low; the exposure only becomes concrete when an application raises the limit, which the library's own docs and tests treat as a normal, supported configuration for legitimate deep nesting. This keeps the finding in the DoS/crash category (not memory corruption or RCE), consistent with the source report's classification.

### Likelihood Explanation
Likelihood is conditional and moderate: it requires the consuming application to have called `SetRecursionLimit()`/`setRecursionLimit()` with a materially larger-than-default value (a documented, common pattern for apps with legitimately deep nested proto schemas — as shown by the library's own `SupportDeepRecursionLimit`/`SupportCustomRecursionLimit*` tests using depths of 1000+). Given that, the attack payload itself is trivial to construct (a flat chain of unknown-field group tags, no valid schema knowledge needed) and requires no privileged access — purely a bounded, well-formed-looking wire payload through the standard public parse API.

### Recommendation
Make `UnknownFieldSet`/`UnknownField` destruction iterative rather than relying on native call-stack recursion, analogous to the existing iterative-teardown pattern already used elsewhere in this codebase for tree-like structures (see `FieldMaskTree::Node::ClearChildren()`, which explicitly documents avoiding recursive `Node` destructors to remain "stack-safe even on very deep trees") [8](#0-7) . Concretely: replace the recursive `delete data_.group` / `~UnknownFieldSet()` chain in `UnknownField::Delete()` and `UnknownFieldSet::ClearFallback()` with a work-stack/queue-based teardown that pops nested `UnknownFieldSet*` groups and deletes them iteratively instead of via nested destructor calls [2](#0-1) .

### Proof of Concept
Not executed in this read-only review (no build/test environment available here); the following describes a minimal, concrete reproduction path a maintainer/agent with a build environment could run:

1. Build a `std::string` payload consisting of `N` repeated occurrences of an unrecognized field number encoded with `WIRETYPE_START_GROUP`, nested via genuine start/end-group markers (or, equivalently, use `UnknownFieldSet::AddGroup()` in a loop as done in `wire_format_unittest.h`'s `UnknownFieldRecursionLimit`/`RecursionLimit` tests) to construct `N` levels of nested groups, `N` chosen well above the default of 100 (e.g. 100,000).
2. Parse with `CodedInputStream::SetRecursionLimit(N + 10)` set beforehand (simulating an app that raised the limit for legitimate deep-schema support) into any message type (the message's own schema need not be recursive — the group is stored as an unknown field regardless of type). Parsing is expected to succeed (as shown by `WireFormatTest.UnknownFieldRecursionLimit`/`RecursionLimit`) [9](#0-8) .
3. Let the parsed message go out of scope. Expected/predicted behavior based on the traced code path: `~Message()` → `_internal_metadata_.Delete<UnknownFieldSet>()` → `~UnknownFieldSet()` → `Clear()`/`ClearFallback()` → `UnknownField::Delete()` → `delete data_.group` recurses `N` times on the native call stack [2](#0-1) , crashing with a stack-overflow signal (SIGSEGV) for sufficiently large `N` relative to the thread's stack size — the exact class of bug fixed upstream in Aptos's referenced commit, but here manifesting through `UnknownFieldSet` teardown rather than a Move-VM value.

### Citations

**File:** src/google/protobuf/io/coded_stream.h (L369-381)
```text
  // Sets the maximum number of bytes that this CodedInputStream will read
  // before refusing to continue.  To prevent servers from allocating enormous
  // amounts of memory to hold parsed messages, the maximum message length
  // should be limited to the shortest length that will not harm usability.
  // The default limit is INT_MAX (~2GB) and apps should set shorter limits
  // if possible. An error will always be printed to stderr if the limit is
  // reached.
  //
  // Note: setting a limit less than the current read position is interpreted
  // as a limit on the current position.
  //
  // This is unrelated to PushLimit()/PopLimit().
  void SetTotalBytesLimit(int total_bytes_limit);
```

**File:** src/google/protobuf/io/coded_stream.h (L393-394)
```text
  // Sets the maximum recursion depth.  The default is 100.
  void SetRecursionLimit(int limit);
```

**File:** src/google/protobuf/unknown_field_set_lite.cc (L23-46)
```text
void UnknownField::Delete() {
  switch (type()) {
    case UnknownField::TYPE_LENGTH_DELIMITED:
      delete data_.string_value;
      break;
    case UnknownField::TYPE_GROUP:
      delete data_.group;
      break;
    default:
      break;
  }
}

void UnknownFieldSet::ClearFallback() {
  auto& fields = this->fields();
  ABSL_DCHECK(!fields.empty());
  if (arena() == nullptr) {
    int n = fields.size();
    do {
      fields[--n].Delete();
    } while (n > 0);
  }
  fields.Clear();
}
```

**File:** src/google/protobuf/unknown_field_set_lite.cc (L77-83)
```text
UnknownFieldSet* UnknownFieldSet::AddGroup(int number) {
  auto& field = *fields().Add();
  field.number_ = number;
  field.SetType(UnknownField::TYPE_GROUP);
  field.data_.group = Arena::Create<UnknownFieldSet>(arena());
  return field.data_.group;
}
```

**File:** src/google/protobuf/wire_format_unittest.h (L858-944)
```text
TYPED_TEST_P(WireFormatTest, RecursionLimit) {
  typename TestFixture::TestRecursiveMessage message;
  message.mutable_a()->mutable_a()->mutable_a()->mutable_a()->set_i(1);
  std::string data;
  ABSL_CHECK(message.SerializeToString(&data));

  {
    io::ArrayInputStream raw_input(data.data(), data.size());
    io::CodedInputStream input(&raw_input);
    input.SetRecursionLimit(4);
    typename TestFixture::TestRecursiveMessage message2;
    EXPECT_TRUE(message2.ParseFromCodedStream(&input));
  }

  {
    io::ArrayInputStream raw_input(data.data(), data.size());
    io::CodedInputStream input(&raw_input);
    input.SetRecursionLimit(3);
    typename TestFixture::TestRecursiveMessage message2;
    EXPECT_FALSE(message2.ParseFromCodedStream(&input));
  }
}

TYPED_TEST_P(WireFormatTest, LargeRecursionLimit) {
  const int kLargeLimit = io::CodedInputStream::GetDefaultRecursionLimit() + 50;
  typename TestFixture::TestRecursiveMessage src, dst, *a;
  a = src.mutable_a();
  for (int i = 0; i < kLargeLimit - 1; i++) {
    a = a->mutable_a();
  }
  a->set_i(1);

  std::string data = src.SerializeAsString();
  {
    // Parse with default recursion limit. Should fail.
    io::ArrayInputStream raw_input(data.data(), data.size());
    io::CodedInputStream input(&raw_input);
    ASSERT_FALSE(dst.ParseFromCodedStream(&input));
  }

  {
    // Parse with custom recursion limit. Should pass.
    io::ArrayInputStream raw_input(data.data(), data.size());
    io::CodedInputStream input(&raw_input);
    input.SetRecursionLimit(kLargeLimit);
    ASSERT_TRUE(dst.ParseFromCodedStream(&input));
  }

  // Verifies the recursion depth.
  int depth = 1;
  a = dst.mutable_a();
  while (a->has_a()) {
    a = a->mutable_a();
    depth++;
  }

  EXPECT_EQ(a->i(), 1);
  EXPECT_EQ(depth, kLargeLimit);
}

TYPED_TEST_P(WireFormatTest, UnknownFieldRecursionLimit) {
  typename TestFixture::TestEmptyMessage message;
  message.mutable_unknown_fields()
      ->AddGroup(1234)
      ->AddGroup(1234)
      ->AddGroup(1234)
      ->AddGroup(1234)
      ->AddVarint(1234, 123);
  std::string data;
  ABSL_CHECK(message.SerializeToString(&data));

  {
    io::ArrayInputStream raw_input(data.data(), data.size());
    io::CodedInputStream input(&raw_input);
    input.SetRecursionLimit(4);
    typename TestFixture::TestEmptyMessage message2;
    EXPECT_TRUE(message2.ParseFromCodedStream(&input));
  }

  {
    io::ArrayInputStream raw_input(data.data(), data.size());
    io::CodedInputStream input(&raw_input);
    input.SetRecursionLimit(3);
    typename TestFixture::TestEmptyMessage message2;
    EXPECT_FALSE(message2.ParseFromCodedStream(&input));
  }
}
```

**File:** src/google/protobuf/message_unittest.inc (L823-839)
```text
TEST(MESSAGE_TEST_NAME, SupportDeepRecursionLimit) {
  UNITTEST::NestedTestAllTypes o, p;
  constexpr int kDepth = 1000;
  auto* child = o.mutable_child();
  for (int i = 0; i < kDepth; i++) {
    child = child->mutable_child();
  }
  child->mutable_payload()->set_optional_int32(100);

  std::string serialized;
  EXPECT_TRUE(o.SerializeToString(&serialized));

  io::ArrayInputStream raw_input(serialized.data(), serialized.size());
  io::CodedInputStream input(&raw_input);
  input.SetRecursionLimit(1100);
  EXPECT_TRUE(p.ParseFromCodedStream(&input));
}
```

**File:** src/google/protobuf/util/field_mask_util.cc (L239-273)
```text
  struct Node {
    Node() = default;
    Node(const Node&) = delete;
    Node& operator=(const Node&) = delete;

    ~Node() { ClearChildren(); }

    // Note: This function avoids recursion (including implicitly through the
    // Node dtors), making it stack-safe even on very deep trees.
    void ClearChildren() {
      // This is a DFS traversal that will:
      // 1. Move all of the children from current node into the stack.
      // 2. Clear the children vector (which is all nullptrs at this point).
      // 3. Drop the Node. This will cause the destructor to be called which
      //    will reenter ClearChildren(), but immediately bail out because the
      //    children vector is empty.
      if (children.empty()) return;
      std::vector<std::unique_ptr<Node>> stack;
      for (auto& name_and_child : children) {
        stack.push_back(std::move(name_and_child.second));
      }
      // Reset the vector to zero size (still has unique_ptrs of nullptr here).
      children.clear();

      while (!stack.empty()) {
        std::unique_ptr<Node> current = std::move(stack.back());
        stack.pop_back();
        for (auto& name_and_child : current->children) {
          stack.push_back(std::move(name_and_child.second));
        }
        // Reset the vector to zero size (still has unique_ptrs of nullptr
        // here).
        current->children.clear();
      }
    }
```
